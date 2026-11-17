from __future__ import annotations

from contextlib import closing, contextmanager
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

from .config import LLMConfig, LLMRunnerConfig, _effective_llm_runners
from .http import HttpClient, _safe_url
from .llm_progress import RequestProgress

_NOTHINK_RE = re.compile(r"(?i)(?<!\S)/nothink(?!\S)")
_THINK_BLOCK_RE = re.compile(r"(?is)<think>.*?(?:</think>|$)")
_CONTINUATION_PROMPT = (
    "Continue exactly from where you stopped. Do not repeat prior text, restart the answer, "
    "or add commentary about continuing. Output only the remaining continuation."
)
_MAX_CONTINUATION_ATTEMPTS = 2
_USAGE_METRIC_PATHS = {
    "completion_tokens": (("completion_tokens",),),
    "prompt_tokens": (("prompt_tokens",),),
    "total_tokens": (("total_tokens",),),
    "cached_tokens": (
        ("cached_tokens",),
        ("prompt_tokens_details", "cached_tokens"),
        ("prompt_cache_hit_tokens",),
        ("cache_read_input_tokens",),
    ),
    "cache_write_tokens": (
        ("cache_write_tokens",),
        ("prompt_tokens_details", "cache_write_tokens"),
        ("cache_creation_input_tokens",),
    ),
    "reasoning_tokens": (
        ("reasoning_tokens",),
        ("completion_tokens_details", "reasoning_tokens"),
    ),
}


@dataclass(frozen=True)
class RunnerStatus:
    index: int
    ip: str
    model: str
    model_tag: str
    concurrent_requests: int
    pending_for_seconds: list[float]
    last_success_at: str | None


class RequestBudgetExceeded(RuntimeError):
    """The next endpoint attempt would exceed this turn's configured budget."""


@dataclass
class _RequestBudget:
    limit: int
    used: int = 0


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
        self._thread_state.reply_footer = ""
        self._thread_state.request_metrics = []
        materialized_messages = list(messages)
        runner_attempts = self._select_runners_for_attempt()
        last_error: Exception | None = None
        for attempt_number, (runner_index, runner) in enumerate(runner_attempts, start=1):
            logging.info(
                "LLM runner request: url=%s model=%s attempt=%s/%s",
                _safe_url(runner.base_url),
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
            except RequestBudgetExceeded:
                raise
            except Exception as exc:
                last_error = exc
                if attempt_number >= len(runner_attempts):
                    raise
                logging.warning(
                    "LLM runner failed: url=%s model=%s attempt=%s/%s reason=%s: %s; trying next runner",
                    _safe_url(runner.base_url),
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

    def pop_last_request_metrics(self) -> list[dict[str, Any]]:
        """Consume this thread's HTTP request metrics, including failed attempts."""
        metrics = getattr(self._thread_state, "request_metrics", [])
        self._thread_state.request_metrics = []
        return metrics

    def set_active_skill_chain_length(self, length: int) -> None:
        self._thread_state.skill_chain_length = max(0, int(length))

    @contextmanager
    def request_budget(self, limit: int):
        """Count actual attempts across calls, continuations, and runner failover.

        Nested scopes also consume their parent budget. Each thread has its own
        scopes, so simultaneous conversations cannot spend one another's budget.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Request budget must be a positive integer")
        budgets = getattr(self._thread_state, "request_budgets", [])
        self._thread_state.request_budgets = budgets
        budgets.append(_RequestBudget(limit))
        try:
            yield
        finally:
            budgets.pop()

    def _consume_request_budget(self) -> None:
        budgets = getattr(self._thread_state, "request_budgets", [])
        for budget in budgets:
            if budget.used >= budget.limit:
                raise RequestBudgetExceeded(
                    f"The limit of {budget.limit} model requests for this turn has been reached."
                )
        for budget in budgets:
            budget.used += 1

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

    @contextmanager
    def force_runner(self, runner_index: int):
        previous_runner_index = getattr(self._thread_state, "forced_runner_index", None)
        self._thread_state.forced_runner_index = runner_index
        try:
            yield
        finally:
            self._thread_state.forced_runner_index = previous_runner_index

    def _select_runners_for_attempt(self) -> list[tuple[int, LLMRunnerConfig]]:
        with self._selection_lock:
            if not self._runners:
                return []
            forced_index = getattr(self._thread_state, "forced_runner_index", None)
            if forced_index is not None:
                if not isinstance(forced_index, int) or not 0 <= forced_index < len(self._runners):
                    raise RuntimeError(f"Requested runner /{forced_index} is not configured")
                return [(forced_index, self._runners[forced_index])]
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
            }
            if self.config.temperature is not None:
                payload["temperature"] = self.config.temperature
            if self.config.max_tokens is not None:
                payload["max_tokens"] = self.config.max_tokens
            payload.update(runner.extra_body)
            payload.setdefault("stream", callable(getattr(self.http_client, "post_json_stream", None)))
            if payload["stream"]:
                payload["stream_options"] = {"include_usage": True, **payload.get("stream_options", {})}
            else:
                payload.pop("stream_options", None)
            if disable_thinking or self._messages_request_no_think(continuation_messages):
                chat_template_kwargs = payload.get("chat_template_kwargs")
                payload["chat_template_kwargs"] = {
                    **(chat_template_kwargs if isinstance(chat_template_kwargs, dict) else {}),
                    "enable_thinking": False,
                }
            self._consume_request_budget()
            started = time.perf_counter()
            request_key = self._register_request(runner_index)
            request_metrics: dict[str, Any] = {
                "request_id": request_key,
                "runner_index": runner_index,
                "model": runner.model,
                "model_tag": runner.model_tag or runner.model,
                "continuation": continuation_attempt > 0,
                "stream": payload["stream"],
                "usage": {},
            }
            progress = RequestProgress(request_key, runner_index, runner.model_tag or runner.model, started)
            template_options = payload.get("chat_template_kwargs")
            thinking = template_options.get("enable_thinking", "default") if isinstance(template_options, dict) else "default"
            with self._selection_lock:
                concurrent_requests = len(self._active_requests[runner_index])
            logging.info(
                "LLM request started: request_id=%s runner=%s model=%s api_model=%s url=%s "
                "continuation=%s/%s chain=%s messages=%s prompt_chars=%s stream=%s "
                "temperature=%s max_tokens=%s max_completion_tokens=%s thinking=%s reasoning_effort=%s "
                "timeout_seconds=%s concurrent_requests=%s",
                request_key, runner_index, runner.model_tag or runner.model, runner.model, _safe_url(url),
                continuation_attempt, _MAX_CONTINUATION_ATTEMPTS,
                getattr(self._thread_state, "skill_chain_length", 0), len(continuation_messages),
                sum(len(self._raw_text(message.get("content"))) for message in continuation_messages),
                payload["stream"], payload.get("temperature", "default"), payload.get("max_tokens", "default"),
                payload.get("max_completion_tokens", "default"), thinking, payload.get("reasoning_effort", "default"),
                getattr(self.http_client, "timeout_seconds", "unknown"), concurrent_requests,
            )

            if self.debug:
                logging.debug("LLM request URL: %s", _safe_url(url))
                logging.debug("LLM request payload: %s", payload)

            try:
                with progress.reporting():
                    if payload["stream"]:
                        data = self._read_streamed_reply(url, payload, headers, progress)
                    else:
                        data = self.http_client.post_json(url, payload, headers=headers)
                        self._observe_response(progress, data)
                    if not isinstance(data, dict):
                        raise RuntimeError("Malformed response from LLM endpoint: expected a JSON object")
                    choice = self._extract_primary_choice(data)
                    request_metrics["finish_reason"] = str(choice.get("finish_reason") or "").strip().lower()
            except BaseException as exc:
                request_metrics["error"] = type(exc).__name__
                raise
            finally:
                duration_ms = (time.perf_counter() - started) * 1000
                request_metrics.update(progress.metrics())
                request_metrics["duration_ms"] = duration_ms
                self._thread_state.request_metrics.append(request_metrics)
                self._unregister_request(runner_index, request_key)
                if "error" in request_metrics:
                    progress.log("failed", level=logging.WARNING, outcome=f"error={request_metrics['error']}")
                else:
                    progress.log("completed", outcome=f"finish_reason={request_metrics['finish_reason'] or 'unknown'}")

            total_duration_ms += duration_ms
            if self.debug:
                logging.debug("LLM response payload: %s", data)
                logging.debug("LLM request completed in %.2fms", duration_ms)

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
                _safe_url(runner.base_url),
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

    def _read_streamed_reply(
        self, url: str, payload: dict[str, Any], headers: dict[str, str], progress: RequestProgress,
    ) -> dict[str, Any]:
        content: list[str] = []
        refusal: list[str] = []
        finish_reason = None
        full_response = None
        saw_delta = False
        with closing(self.http_client.post_json_stream(url, payload, headers=headers)) as events:
            for data in events:
                if not isinstance(data, dict):
                    raise RuntimeError("Malformed response from LLM endpoint: expected a JSON object")
                self._observe_response(progress, data, streamed=True)
                if data.get("error") is not None:
                    raise RuntimeError(f"LLM endpoint returned a streaming error from {_safe_url(url)}")
                choices = data.get("choices")
                if not isinstance(choices, list) or any(not isinstance(choice, dict) for choice in choices):
                    raise RuntimeError("Malformed response from LLM endpoint: expected a choices list")
                for choice in choices:
                    if choice.get("index", 0) != 0:
                        continue
                    if "message" in choice:
                        if saw_delta or full_response is not None:
                            raise RuntimeError("Malformed response from LLM endpoint: mixed streaming and JSON responses")
                        self._extract_primary_choice(data)
                        full_response = data
                        continue
                    if full_response is not None:
                        raise RuntimeError("Malformed response from LLM endpoint: mixed streaming and JSON responses")
                    delta = choice.get("delta")
                    if delta is None and choice.get("finish_reason"):
                        delta = {}
                    if not isinstance(delta, dict):
                        raise RuntimeError("Malformed response from LLM endpoint: expected a delta object")
                    saw_delta = True
                    content_part = self._raw_text(delta.get("content"))
                    if content_part:
                        content.append(content_part)
                    refusal_part = self._raw_text(delta.get("refusal"))
                    if refusal_part:
                        refusal.append(refusal_part)
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        if full_response is not None:
            return full_response
        if not saw_delta or not finish_reason:
            raise RuntimeError("Incomplete response from LLM endpoint: missing completion finish reason")
        return {
            "choices": [{
                "message": {"content": "".join(content) if content else None, "refusal": "".join(refusal)},
                "finish_reason": finish_reason,
            }],
            "usage": progress.metrics()["usage"],
        }

    @classmethod
    def _observe_response(cls, progress: RequestProgress, data: Any, *, streamed: bool = False) -> None:
        if not isinstance(data, dict):
            return
        usage: dict[str, int] = {}
        cls._merge_usage_counts(usage, data.get("usage"))
        choices = data.get("choices")
        choice = next((
            item for item in choices if isinstance(item, dict) and item.get("index", 0) == 0
        ), {}) if isinstance(choices, list) else {}
        message = choice.get("delta", choice.get("message"))
        if not isinstance(message, dict):
            message = {}
        progress.update(
            content=cls._raw_text(message.get("content")) or cls._raw_text(message.get("refusal")),
            reasoning=cls._raw_text(message.get("reasoning_content")) or cls._raw_text(message.get("reasoning")),
            usage=usage,
            streamed=streamed and "message" not in choice,
        )

    @staticmethod
    def _raw_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                item["text"] for item in content
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
            )
        return ""

    @staticmethod
    def _extract_primary_choice(data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RuntimeError("Malformed response from LLM endpoint: expected a non-empty choices list")
        choice = choices[0]
        if not isinstance(choice.get("message"), dict):
            raise RuntimeError("Malformed response from LLM endpoint: expected a message object")
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
        for field, paths in _USAGE_METRIC_PATHS.items():
            value = OpenAICompatibleClient._extract_usage_token_count(usage, paths)
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
        skill_chain_length = max(0, int(getattr(self._thread_state, "skill_chain_length", 0) or 0))
        segments = [
            f"IP: {self._runner_ip(runner)}",
            f"model: {runner.model_tag or runner.model} (chain: {skill_chain_length})",
        ]
        performance = self._format_performance_metrics(data, duration_ms)
        if performance:
            segments.append(performance)
        usage_details = self._format_usage_details(data)
        if usage_details:
            segments.extend(usage_details)
        return " | ".join(segments)

    @staticmethod
    def _format_usage_details(data: dict[str, Any]) -> list[str]:
        usage = data.get("usage")
        cached_tokens = OpenAICompatibleClient._extract_usage_token_count(usage, (("cached_tokens",),))
        cache_write_tokens = OpenAICompatibleClient._extract_usage_token_count(usage, (("cache_write_tokens",),))
        reasoning_tokens = OpenAICompatibleClient._extract_usage_token_count(usage, (("reasoning_tokens",),))
        details: list[str] = []
        if cached_tokens or cache_write_tokens:
            details.append(f"cache: {cached_tokens or 0} hit, {cache_write_tokens or 0} write")
        if reasoning_tokens:
            details.append(f"reasoning: {reasoning_tokens} tok")
        return details

    @staticmethod
    def _format_performance_metrics(data: dict[str, Any], duration_ms: float) -> str:
        duration_seconds = max(duration_ms / 1000.0, 0.0)
        usage = data.get("usage")
        completion_tokens = OpenAICompatibleClient._extract_usage_token_count(
            usage, (("completion_tokens",),)
        )
        total_tokens = OpenAICompatibleClient._extract_usage_token_count(usage, (("total_tokens",),))
        output_tokens = completion_tokens if completion_tokens and completion_tokens > 0 else total_tokens
        if output_tokens and output_tokens > 0 and duration_seconds > 0:
            return f"{output_tokens / duration_seconds:.1f} tok/s, {output_tokens} tok, {duration_seconds:.2f}s"
        if duration_seconds > 0:
            return f"{duration_seconds:.2f}s"
        return ""

    @staticmethod
    def _extract_usage_token_count(
        usage: Any,
        paths: tuple[tuple[str, ...], ...],
    ) -> int | None:
        if not isinstance(usage, dict):
            return None
        for path in paths:
            value: Any = usage
            for field in path:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(field)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and value >= 0:
                return value
            if isinstance(value, float) and value.is_integer() and value >= 0:
                return int(value)
            if isinstance(value, str):
                try:
                    parsed = int(value.strip())
                    if parsed >= 0:
                        return parsed
                except ValueError:
                    continue
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
