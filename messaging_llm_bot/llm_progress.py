from __future__ import annotations

from contextlib import contextmanager
import logging
import re
import threading
import time
from typing import Any

_PROGRESS_INTERVAL_SECONDS = 5.0
_PREVIEW_CHARS = 400


class RequestProgress:
    """Bounded console previews and a heartbeat that also runs during blocking reads."""

    def __init__(self, request_id: int, runner_index: int, model: str, started: float) -> None:
        self.request_id = request_id
        self.runner_index = runner_index
        self.model = model
        self.started = started
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_activity = started
        self._first_token_ms: float | None = None
        self._chunks = 0
        self._phase = "waiting_for_output"
        self._content_chars = 0
        self._reasoning_chars = 0
        self._latest_content = ""
        self._latest_reasoning = ""
        self._usage: dict[str, int] = {}
        self._in_think = False
        self._pending_content = ""

    @contextmanager
    def reporting(self):
        thread = None
        if logging.getLogger().isEnabledFor(logging.INFO):
            thread = threading.Thread(target=self._heartbeat, name=f"llm-progress-{self.request_id}", daemon=True)
            thread.start()
        try:
            yield
        finally:
            self._stop.set()
            if thread is not None:
                thread.join()
            with self._lock:
                self._append_text(self._pending_content, reasoning=self._in_think)
                self._pending_content = ""

    def _heartbeat(self) -> None:
        while not self._stop.wait(_PROGRESS_INTERVAL_SECONDS):
            self.log("progress")

    def update(
        self, *, content: str = "", reasoning: str = "", usage: dict[str, int] | None = None,
        streamed: bool = False,
    ) -> None:
        first_token = False
        with self._lock:
            self._last_activity = time.perf_counter()
            if streamed:
                self._chunks += 1
                if self._first_token_ms is None and (content or reasoning):
                    self._first_token_ms = (self._last_activity - self.started) * 1000
                    first_token = True
            if usage:
                # Streaming usage reports are cumulative snapshots, not increments.
                self._usage.update(usage)
            self._append_text(reasoning, reasoning=True)
            self._pending_content += content
            while self._pending_content:
                marker = "</think>" if self._in_think else "<think>"
                match = re.search(marker, self._pending_content, re.IGNORECASE)
                if match is not None:
                    self._append_text(self._pending_content[:match.start()], reasoning=self._in_think)
                    self._pending_content = self._pending_content[match.end():]
                    self._in_think = not self._in_think
                    continue
                # Keep a possible split tag until the next chunk arrives.
                partial = max((
                    size for size in range(1, min(len(marker), len(self._pending_content) + 1))
                    if marker.startswith(self._pending_content[-size:].lower())
                ), default=0)
                length = len(self._pending_content) - partial
                self._append_text(self._pending_content[:length], reasoning=self._in_think)
                self._pending_content = self._pending_content[length:]
                break
        if first_token:
            self.log("first token")

    def _append_text(self, text: str, *, reasoning: bool) -> None:
        if not text:
            return
        if reasoning:
            self._reasoning_chars += len(text)
            self._latest_reasoning = (self._latest_reasoning + text)[-_PREVIEW_CHARS:]
            self._phase = "thinking"
        else:
            self._content_chars += len(text)
            self._latest_content = (self._latest_content + text)[-_PREVIEW_CHARS:]
            self._phase = "answering"

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            metrics = {
                "usage": dict(self._usage),
                "stream_chunks": self._chunks,
                "content_chars": self._content_chars,
                "reasoning_chars": self._reasoning_chars,
            }
            if self._first_token_ms is not None:
                metrics["first_token_ms"] = self._first_token_ms
            return metrics

    def log(self, event: str, *, level: int = logging.INFO, outcome: str = "") -> None:
        now = time.perf_counter()
        with self._lock:
            elapsed = max(now - self.started, 0.0)
            first_token = f"{self._first_token_ms / 1000:.2f}s" if self._first_token_ms is not None else "unavailable"
            tokens = self._usage.get("completion_tokens")
            tokens_per_second = f"{tokens / elapsed:.1f}" if tokens is not None and elapsed > 0 else "unavailable"
            usage = " ".join(f"{key}={value}" for key, value in self._usage.items()) or "usage=unavailable"
            char_rate = (self._content_chars + self._reasoning_chars) / elapsed if elapsed > 0 else 0.0
            values = (
                event, self.request_id, self.runner_index, self.model, self._phase, elapsed,
                max(now - self._last_activity, 0.0), first_token, self._chunks, self._content_chars,
                self._reasoning_chars, char_rate, tokens_per_second, usage,
                self._latest_reasoning, self._latest_content, f" {outcome}" if outcome else "",
            )
        # repr escapes newlines and terminal control characters in model-provided text.
        logging.log(
            level,
            "LLM request %s: request_id=%s runner=%s model=%s phase=%s elapsed=%.2fs idle=%.2fs "
            "first_token=%s chunks=%s content_chars=%s reasoning_chars=%s chars_per_second=%.1f "
            "tokens_per_second=%s %s latest_reasoning=%r latest_output=%r%s",
            *values,
        )
