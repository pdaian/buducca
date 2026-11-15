from __future__ import annotations

import json
import re
import socket
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


class RequestTimeoutError(RuntimeError):
    """Raised when an HTTP request times out."""


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

    def get_json(self, url: str) -> dict[str, Any]:
        req = Request(url=url, method="GET")
        return self._request_json(req)

    def get_bytes(self, url: str) -> bytes:
        req = Request(url=url, method="GET")
        return self._request_bytes(req)

    def _request_bytes(self, req: Request) -> bytes:
        safe_url = _safe_url(req.full_url)
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                return response.read()
        except (socket.timeout, TimeoutError):
            raise RequestTimeoutError(f"Request timed out for {safe_url}") from None
        except HTTPError as err:
            err.close()
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
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError(f"Non-JSON response from {_safe_url(req.full_url)}") from None
        if not isinstance(payload, dict):
            raise RuntimeError(f"Expected a JSON object from {_safe_url(req.full_url)}")
        return payload
