from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

from .config import LLMConfig, LLMRunnerConfig, _effective_llm_runners
from .http import HttpClient

_NOTHINK_RE = re.compile(r"(?i)(?<!\S)/nothink(?!\S)")
_THINK_BLOCK_RE = re.compile(r"(?is)<think>.*?(?:</think>|$)")


@dataclass(frozen=True)
class RunnerStatus:
    index: int
    ip: str
    model: str
    model_tag: str
    concurrent_requests: int
    pending_for_seconds: list[float]


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig, http_client: HttpClient, *, debug: bool = False) -> None:
        self.config = config
        self.http_client = http_client
        self.debug = debug
        self._runners = _effective_llm_runners(config)
        self._selection_lock = threading.Lock()
        self._next_runner_index = 0
        self._active_requests: dict[int, dict[int, float]] = {index: {} for index in range(len(self._runners))}
        self._request_id = 0
        self._thread_state = threading.local()

    def generate_reply(self, messages: Iterable[dict[str, str]], *, disable_thinking: bool = False) -> str:
        materialized_messages = list(messages)
        self._thread_state.reply_footer = ""
        runner_attempts = self._select_runners_for_attempt()
        last_error: Exception | None = None
        for attempt_number, (runner_index, runner) in enumerate(runner_attempts, start=1):
            try:
                return self._generate_reply_with_runner(
                    runner_index,
                    runner,
                    materialized_messages,
                    disable_thinking=disable_thinking,
                )
            except Exception as exc:
                last_error = exc
                if attempt_number >= len(runner_attempts):
                    raise
                logging.warning(
                    "LLM runner failed: url=%s model=%s attempt=%s/%s; trying next runner",
                    runner.base_url,
                    runner.model_tag or runner.model,
                    attempt_number,
                    len(runner_attempts),
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("No LLM runners configured")

    def pop_last_reply_footer(self) -> str:
        footer = getattr(self._thread_state, "reply_footer", "")
        self._thread_state.reply_footer = ""
        return footer

    def get_runner_statuses(self) -> list[RunnerStatus]:
        now = time.monotonic()
        with self._selection_lock:
            statuses: list[RunnerStatus] = []
            for index, runner in enumerate(self._runners):
                active = self._active_requests[index]
                pending = sorted(now - started for started in active.values())
                statuses.append(
                    RunnerStatus(
                        index=index,
                        ip=self._runner_ip(runner),
                        model=runner.model,
                        model_tag=runner.model_tag or runner.model,
                        concurrent_requests=len(active),
                        pending_for_seconds=pending,
                    )
                )
        return statuses

    def _select_runner(self) -> tuple[int, LLMRunnerConfig]:
        with self._selection_lock:
            if len(self._runners) == 1:
                return 0, self._runners[0]
            runner_index = self._next_runner_index % len(self._runners)
            self._next_runner_index += 1
            return runner_index, self._runners[runner_index]

    def _select_runners_for_attempt(self) -> list[tuple[int, LLMRunnerConfig]]:
        with self._selection_lock:
            if not self._runners:
                return []
            start_index = self._next_runner_index % len(self._runners)
            self._next_runner_index += 1
            return [
                (runner_index, self._runners[runner_index])
                for runner_index in (
                    (start_index + offset) % len(self._runners) for offset in range(len(self._runners))
                )
            ]

    def _generate_reply_with_runner(
        self,
        runner_index: int,
        runner: LLMRunnerConfig,
        messages: list[dict[str, str]],
        *,
        disable_thinking: bool,
    ) -> str:
        payload = {
            "model": runner.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if disable_thinking or self._messages_request_no_think(messages):
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Authorization": f"Bearer {runner.api_key}"}
        endpoint = runner.endpoint_path
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        url = runner.base_url.rstrip("/") + endpoint
        started = time.perf_counter()
        request_key = self._register_request(runner_index)

        if self.debug:
            logging.debug("LLM request URL: %s", url)
            logging.debug("LLM request payload: %s", payload)

        try:
            data = self.http_client.post_json(url, payload, headers=headers)
            duration_ms = (time.perf_counter() - started) * 1000
            if self.debug:
                logging.debug("LLM response payload: %s", data)
                logging.debug("LLM request completed in %.2fms", duration_ms)

            try:
                message = data["choices"][0]["message"]
            except (KeyError, IndexError, AttributeError) as err:
                raise RuntimeError(f"Malformed response from LLM endpoint: {data}") from err
            self._thread_state.reply_footer = self._format_reply_footer(runner)
            content = message.get("content")
            if isinstance(content, str):
                return self._sanitize_reply_text(content)
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("type") or "").strip().lower()
                    if item_type != "text":
                        continue
                    text = item.get("text")
                    if isinstance(text, str):
                        sanitized = self._sanitize_reply_text(text)
                        if sanitized:
                            parts.append(sanitized)
                return "\n".join(parts).strip()
            refusal = message.get("refusal")
            if isinstance(refusal, str):
                return self._sanitize_reply_text(refusal)
            return ""
        finally:
            self._unregister_request(runner_index, request_key)

    def _register_request(self, runner_index: int) -> int:
        with self._selection_lock:
            self._request_id += 1
            request_key = self._request_id
            self._active_requests[runner_index][request_key] = time.monotonic()
            return request_key

    def _unregister_request(self, runner_index: int, request_key: int) -> None:
        with self._selection_lock:
            self._active_requests[runner_index].pop(request_key, None)

    @staticmethod
    def _runner_ip(runner: LLMRunnerConfig) -> str:
        return urlparse(runner.base_url).hostname or "unknown"

    def _format_reply_footer(self, runner: LLMRunnerConfig) -> str:
        return f"IP: {self._runner_ip(runner)} | model: {runner.model_tag or runner.model}"

    @staticmethod
    def _messages_request_no_think(messages: Iterable[dict[str, str]]) -> bool:
        for message in messages:
            content = message.get("content")
            if isinstance(content, str) and _NOTHINK_RE.search(content):
                return True
        return False

    @staticmethod
    def _sanitize_reply_text(text: str) -> str:
        return _THINK_BLOCK_RE.sub("", text).strip()
