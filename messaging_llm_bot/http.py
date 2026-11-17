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


class ContextLengthExceededError(RuntimeError):
    """The endpoint explicitly rejected a request that exceeded its context window."""


def _is_context_length_error(payload: Any) -> bool:
    """Recognize provider errors without exposing their potentially private bodies."""
    message = ""
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
            if any(code in ("context_length_exceeded", "exceed_context_size_error") for code in codes):
                return True
            message = error.get("message", "")
        elif isinstance(error, str):
            message = error
        else:
            return False
    elif payload is not None:
        return False
    return isinstance(message, str) and bool(re.search(
        r"request \(\d+ tokens\) exceeds the available context size \(\d+ tokens\)"
        r"|trying to keep the first \d+ tokens when (?:the )?context (?:the )?(?:length|size) is only \d+"
        r"|maximum context length is \d+ tokens.{0,100}(?:requested|resulted in) \d+ tokens",
        message, re.IGNORECASE | re.DOTALL,
    ))


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
            raise RuntimeError(f"Incomplete event stream from {safe_url}")

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
                raw = err.read(8192) if err.code in (400, 413) else b""
            except (OSError, ValueError, HTTPException):
                raw = b""
            finally:
                err.close()
            if err.code in (400, 413) and _is_context_length_error(raw):
                raise ContextLengthExceededError(
                    f"HTTP {err.code} for {safe_url}: model context window exceeded"
                ) from None
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
