from __future__ import annotations

import json
import socket
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class RequestTimeoutError(RuntimeError):
    """Raised when an HTTP request times out."""


class HttpClient:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)

        body = json.dumps(payload).encode("utf-8")
        req = Request(url=url, data=body, headers=request_headers, method="POST")
        return self._request_json(req)

    def get_json(self, url: str) -> dict[str, Any]:
        req = Request(url=url, method="GET")
        return self._request_json(req)

    def get_bytes(self, url: str) -> bytes:
        req = Request(url=url, method="GET")
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                return response.read()
        except (socket.timeout, TimeoutError) as err:
            raise RequestTimeoutError(f"Request timed out for {req.full_url}") from err
        except HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {err.code} for {req.full_url}: {detail}") from err
        except URLError as err:
            raise RuntimeError(f"Failed request to {req.full_url}: {err}") from err

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
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except (socket.timeout, TimeoutError) as err:
            raise RequestTimeoutError(f"Request timed out for {req.full_url}") from err
        except HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {err.code} for {req.full_url}: {detail}") from err
        except URLError as err:
            raise RuntimeError(f"Failed request to {req.full_url}: {err}") from err

        try:
            return json.loads(raw)
        except json.JSONDecodeError as err:
            raise RuntimeError(f"Non-JSON response from {req.full_url}: {raw[:300]}") from err
