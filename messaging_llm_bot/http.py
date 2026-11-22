from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPException
import json
import re
import socket
import uuid
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


class RequestTimeoutError(RuntimeError):
    """Raised when an HTTP request times out."""


class StreamError(RuntimeError):
    """A streamed response failed before a complete reply was received."""


class ContextLengthExceededError(RuntimeError):
    """The endpoint reported exhausted context capacity, possibly its KV cache."""

    def __init__(self, message: str, *, details: dict[str, int] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})

    @classmethod
    def from_payload(cls, payload: Any, message: str) -> ContextLengthExceededError | None:
        details = _context_length_error_details(payload)
        return cls(message, details=details) if details is not None else None


def _context_length_error_details(payload: Any) -> dict[str, int] | None:
    """Retain only known numeric fields, never the provider's private error body."""
    message = ""
    details: dict[str, int] = {}
    recognized = False
    if isinstance(payload, bytes):
        message = payload.decode("utf-8", errors="replace")
        try:
            payload = json.loads(message)
        except (ValueError, RecursionError):
            payload = None
    if isinstance(payload, dict):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            codes = (error.get("code"), error.get("type"))
            recognized = any(code in ("context_length_exceeded", "exceed_context_size_error") for code in codes)
            for source, target, minimum in (
                ("n_ctx", "context_window_tokens", 1), ("n_prompt_tokens", "prompt_tokens", 0),
            ):
                value = error.get(source)
                if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
                    details[target] = value
            message = error.get("message", "")
        elif isinstance(error, str):
            message = error
        else:
            return None
    elif payload is not None:
        return None
    if isinstance(message, str):
        patterns = (
            r"request \((?P<prompt_tokens>[0-9]+) tokens\) exceeds the available context size "
            r"\((?P<context_window_tokens>[0-9]+) tokens\)",
            r"maximum context length is (?P<context_window_tokens>[0-9]+) tokens.{0,100}"
            r"(?:requested|resulted in) (?P<requested_tokens>[0-9]+) tokens"
            r"(?:\s*\((?P<prompt_tokens>[0-9]+) in (?:the )?messages\b)?",
            r"trying to keep the first [0-9]+ tokens when (?:the )?context (?:the )?(?:length|size) "
            r"is only (?P<context_window_tokens>[0-9]+)",
            r"\bcontext (?:size|length) has been exceeded\b",
        )
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE | re.DOTALL)
            if match is None:
                continue
            recognized = True
            for key, value in match.groupdict().items():
                if value is not None and len(value) <= 20:
                    count = int(value)
                    if count > 0 or key == "prompt_tokens":
                        details.setdefault(key, count)
            break
    return details if recognized else None


def _safe_url(url: str) -> str:
    """Remove URL credentials, query secrets, and Telegram's token path segment."""
    parsed = urlsplit(url)
    path = re.sub(r"/bot[^/]+", "/bot<redacted>", parsed.path)
    return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], path, "", ""))


class HttpClient:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)

        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        req = Request(url=url, data=body, headers=request_headers, method="POST")
        return self._request_json(req)

    def post_json_stream(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Read Chat Completions SSE events, or JSON if the server ignores streaming."""
        request_headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if headers:
            request_headers.update(headers)
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        req = Request(url=url, data=body, headers=request_headers, method="POST")
        safe_url = _safe_url(url)
        try:
            with self._open_response(req) as response:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type == "application/json":
                    yield self._decode_json(response.read(), safe_url)
                    return
                data_lines: list[bytes] = []
                for line in response:
                    line = line.rstrip(b"\r\n")
                    if line.startswith(b"data:"):
                        data_lines.append(line[5:].removeprefix(b" "))
                    elif not line and data_lines:
                        raw = b"\n".join(data_lines)
                        data_lines.clear()
                        if raw.strip() == b"[DONE]":
                            return
                        yield self._decode_json(raw, safe_url)
                # A dropped connection must not turn a partial answer into a success.
                if data_lines and b"\n".join(data_lines).strip() == b"[DONE]":
                    return
                raise StreamError(f"Incomplete event stream from {safe_url}")
        except (OSError, HTTPException):
            raise StreamError(f"Interrupted event stream from {safe_url}") from None

    def get_json(self, url: str) -> dict[str, Any]:
        req = Request(url=url, method="GET")
        return self._request_json(req)

    def get_bytes(self, url: str) -> bytes:
        req = Request(url=url, method="GET")
        return self._request_bytes(req)

    def _request_bytes(self, req: Request) -> bytes:
        with self._open_response(req) as response:
            return response.read()

    @contextmanager
    def _open_response(self, req: Request):
        safe_url = _safe_url(req.full_url)
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                yield response
        except (socket.timeout, TimeoutError):
            raise RequestTimeoutError(f"Request timed out for {safe_url}") from None
        except HTTPError as err:
            try:
                # llama.cpp also reports exhausted KV cache as a server error.
                raw = err.read(8192) if err.code in (400, 413, 500) else b""
            except (OSError, ValueError, HTTPException):
                raw = b""
            finally:
                err.close()
            context_error = ContextLengthExceededError.from_payload(
                raw, f"HTTP {err.code} for {safe_url}: model context window exceeded",
            )
            if context_error is not None:
                raise context_error from None
            raise RuntimeError(f"HTTP {err.code} for {safe_url}") from None
        except URLError as err:
            if isinstance(err.reason, (socket.timeout, TimeoutError)):
                raise RequestTimeoutError(f"Request timed out for {safe_url}") from None
            raise RuntimeError(f"Failed request to {safe_url}: {type(err.reason).__name__}") from None

    def post_multipart(
        self,
        url: str,
        *,
        fields: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]],
    ) -> dict[str, Any]:
        boundary = f"----codex-{uuid.uuid4().hex}"
        body = bytearray()
        for key, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        for key, (filename, payload, content_type) in files.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            disposition = f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
            body.extend(disposition.encode("utf-8"))
            body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
            body.extend(payload)
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        req = Request(
            url=url,
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        return self._request_json(req)

    def _request_json(self, req: Request) -> dict[str, Any]:
        raw = self._request_bytes(req)
        return self._decode_json(raw, _safe_url(req.full_url))

    @staticmethod
    def _decode_json(raw: bytes, safe_url: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError(f"Non-JSON response from {safe_url}") from None
        if not isinstance(payload, dict):
            raise RuntimeError(f"Expected a JSON object from {safe_url}")
        return payload
