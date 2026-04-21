from __future__ import annotations

from contextlib import contextmanager
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

from .config import LLMConfig, LLMRunnerConfig, _effective_llm_runners
from .http import HttpClient

_NOTHINK_RE = re.compile(r"(?i)(?<!\S)/nothink(?!\S)")
_THINK_BLOCK_RE = re.compile(r"(?is)<think>.*?(?:</think>|$)")
_CONTINUATION_PROMPT = (
    "Continue exactly from where you stopped. Do not repeat prior text, restart the answer, "
    "or add commentary about continuing. Output only the remaining continuation."
)
_MAX_CONTINUATION_ATTEMPTS = 2


@dataclass(frozen=True)
class RunnerStatus:
    index: int
    ip: str
    model: str
    model_tag: str
    concurrent_requests: int
    pending_for_seconds: list[float]
    last_success_at: str | None


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig, http_client: HttpClient, *, debug: bool = False) -> None:
        self.config = config
        self.http_client = http_client
        self.debug = debug
        self._runners = _effective_llm_runners(config)
        self._selection_lock = threading.Lock()
        self._next_runner_index = 0
        self._active_requests: dict[int, dict[int, float]] = {index: {} for index in range(len(self._runners))}
        self._last_success_at: dict[int, str | None] = {index: None for index in range(len(self._runners))}
        self._request_id = 0
        self._thread_state = threading.local()

    def generate_reply(self, messages: Iterable[dict[str, str]], *, disable_thinking: bool = False) -> str:
        materialized_messages = list(messages)
        self._thread_state.reply_footer = ""
        runner_attempts = self._select_runners_for_attempt()
        last_error: Exception | None = None
        for attempt_number, (runner_index, runner) in enumerate(runner_attempts, start=1):
            logging.info(
                "LLM runner request: url=%s model=%s attempt=%s/%s",
                runner.base_url,
                runner.model_tag or runner.model,
                attempt_number,
                len(runner_attempts),
            )
            try:
                reply = self._generate_reply_with_runner(
                    runner_index,
                    runner,
                    materialized_messages,
                    disable_thinking=disable_thinking,
                )
                if getattr(self._thread_state, "chain_runner_affinity_active", False):
                    self._thread_state.chain_runner_index = runner_index
                return reply
            except Exception as exc:
                last_error = exc
                if attempt_number >= len(runner_attempts):
                    raise
                logging.warning(
                    "LLM runner failed: url=%s model=%s attempt=%s/%s reason=%s: %s; trying next runner",
                    runner.base_url,
                    runner.model_tag or runner.model,
                    attempt_number,
                    len(runner_attempts),
                    type(exc).__name__,
                    exc,
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
                        last_success_at=self._last_success_at[index],
                    )
                )
        return statuses

    @contextmanager
    def chain_runner_affinity(self):
        previous_active = getattr(self._thread_state, "chain_runner_affinity_active", False)
        previous_runner_index = getattr(self._thread_state, "chain_runner_index", None)
        self._thread_state.chain_runner_affinity_active = True
        self._thread_state.chain_runner_index = None
        try:
            yield
        finally:
            self._thread_state.chain_runner_affinity_active = previous_active
            self._thread_state.chain_runner_index = previous_runner_index

    def _select_runners_for_attempt(self) -> list[tuple[int, LLMRunnerConfig]]:
        with self._selection_lock:
            if not self._runners:
                return []
            pinned_index = getattr(self._thread_state, "chain_runner_index", None)
            if (
                getattr(self._thread_state, "chain_runner_affinity_active", False)
                and isinstance(pinned_index, int)
                and 0 <= pinned_index < len(self._runners)
            ):
                start_index = pinned_index
            else:
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
        headers = {"Authorization": f"Bearer {runner.api_key}"}
        endpoint = runner.endpoint_path
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        url = runner.base_url.rstrip("/") + endpoint
        continuation_messages = list(messages)
        aggregated_reply = ""
        total_duration_ms = 0.0
        aggregated_usage: dict[str, int] = {}

        for continuation_attempt in range(_MAX_CONTINUATION_ATTEMPTS + 1):
            payload = {
                "model": runner.model,
                "messages": continuation_messages,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            }
            if disable_thinking or self._messages_request_no_think(continuation_messages):
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            started = time.perf_counter()
            request_key = self._register_request(runner_index)

            if self.debug:
                logging.debug("LLM request URL: %s", url)
                logging.debug("LLM request payload: %s", payload)

            try:
                data = self.http_client.post_json(url, payload, headers=headers)
            finally:
                self._unregister_request(runner_index, request_key)

            duration_ms = (time.perf_counter() - started) * 1000
            total_duration_ms += duration_ms
            if self.debug:
                logging.debug("LLM response payload: %s", data)
                logging.debug("LLM request completed in %.2fms", duration_ms)

            choice = self._extract_primary_choice(data)
            with self._selection_lock:
                self._last_success_at[runner_index] = datetime.now(timezone.utc).isoformat()
            self._merge_usage_counts(aggregated_usage, data.get("usage"))
            reply_part = self._extract_reply_text(choice.get("message"))
            if reply_part:
                aggregated_reply += reply_part

            finish_reason = str(choice.get("finish_reason") or "").strip().lower()
            if not self._should_continue_after_finish_reason(finish_reason, reply_part):
                self._thread_state.reply_footer = self._format_reply_footer(
                    runner,
                    data={"usage": aggregated_usage},
                    duration_ms=total_duration_ms,
                )
                return aggregated_reply

            logging.warning(
                "LLM completion truncated: url=%s model=%s finish_reason=%s continuation_attempt=%s/%s",
                runner.base_url,
                runner.model_tag or runner.model,
                finish_reason or "unknown",
                continuation_attempt + 1,
                _MAX_CONTINUATION_ATTEMPTS,
            )
            continuation_messages = [
                *messages,
                {"role": "assistant", "content": aggregated_reply},
                {"role": "user", "content": _CONTINUATION_PROMPT},
            ]

        self._thread_state.reply_footer = self._format_reply_footer(
            runner,
            data={"usage": aggregated_usage},
            duration_ms=total_duration_ms,
        )
        return aggregated_reply

    @staticmethod
    def _extract_primary_choice(data: dict[str, Any]) -> dict[str, Any]:
        try:
            choice = data["choices"][0]
        except (KeyError, IndexError, AttributeError) as err:
            raise RuntimeError(f"Malformed response from LLM endpoint: {data}") from err
        if not isinstance(choice, dict):
            raise RuntimeError(f"Malformed response from LLM endpoint: {data}")
        return choice

    @classmethod
    def _extract_reply_text(cls, message: Any) -> str:
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return cls._sanitize_reply_text(content)
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
                    sanitized = cls._sanitize_reply_text(text)
                    if sanitized:
                        parts.append(sanitized)
            return "\n".join(parts).strip()
        refusal = message.get("refusal")
        if isinstance(refusal, str):
            return cls._sanitize_reply_text(refusal)
        return ""

    @staticmethod
    def _should_continue_after_finish_reason(finish_reason: str, reply_part: str) -> bool:
        return bool(reply_part.strip()) and finish_reason in {"length", "max_tokens"}

    @staticmethod
    def _merge_usage_counts(destination: dict[str, int], usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for field in ("completion_tokens", "prompt_tokens", "total_tokens"):
            value = OpenAICompatibleClient._extract_usage_token_count(usage, field)
            if value is None:
                continue
            destination[field] = destination.get(field, 0) + value

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

    def _format_reply_footer(self, runner: LLMRunnerConfig, *, data: dict[str, Any], duration_ms: float) -> str:
        segments = [
            f"IP: {self._runner_ip(runner)}",
            f"model: {runner.model_tag or runner.model}",
        ]
        performance = self._format_performance_metrics(data, duration_ms)
        if performance:
            segments.append(performance)
        return " | ".join(segments)

    @staticmethod
    def _format_performance_metrics(data: dict[str, Any], duration_ms: float) -> str:
        duration_seconds = max(duration_ms / 1000.0, 0.0)
        usage = data.get("usage")
        completion_tokens = OpenAICompatibleClient._extract_usage_token_count(usage, "completion_tokens")
        total_tokens = OpenAICompatibleClient._extract_usage_token_count(usage, "total_tokens")
        output_tokens = completion_tokens if completion_tokens and completion_tokens > 0 else total_tokens
        if output_tokens and output_tokens > 0 and duration_seconds > 0:
            return f"{output_tokens / duration_seconds:.1f} tok/s, {output_tokens} tok, {duration_seconds:.2f}s"
        if duration_seconds > 0:
            return f"{duration_seconds:.2f}s"
        return ""

    @staticmethod
    def _extract_usage_token_count(usage: Any, field: str) -> int | None:
        if not isinstance(usage, dict):
            return None
        value = usage.get(field)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return None
        return None

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
