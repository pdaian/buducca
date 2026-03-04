from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
        except HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {err.code} for {req.full_url}: {detail}") from err
        except URLError as err:
            raise RuntimeError(f"Failed request to {req.full_url}: {err}") from err

    def _request_json(self, req: Request) -> dict[str, Any]:
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {err.code} for {req.full_url}: {detail}") from err
        except URLError as err:
            raise RuntimeError(f"Failed request to {req.full_url}: {err}") from err

        try:
            return json.loads(raw)
        except json.JSONDecodeError as err:
            raise RuntimeError(f"Non-JSON response from {req.full_url}: {raw[:300]}") from err
