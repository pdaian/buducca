from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.workspace import Workspace

NAME = "fetch_url"
DESCRIPTION = (
    "Download the contents of a specific URL using Python stdlib urllib. "
    "Supports urllib-handled schemes including http, https, file, ftp, and data. "
    "Args: url (required), timeout_seconds (optional, default 20), max_bytes (optional, default 1048576)."
)
ARGS_SCHEMA = (
    '{"url":"required","timeout_seconds":20,"max_bytes":1048576}'
)

_DEFAULT_TIMEOUT_SECONDS = 20
_DEFAULT_MAX_BYTES = 1024 * 1024
_TEXTUAL_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/javascript",
)


def _resolve_positive_int(value: Any, *, field_name: str, default: int) -> int:
    raw_value = default if value is None else value
    try:
        resolved = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"`{field_name}` must be an integer greater than 0.") from exc
    if resolved <= 0:
        raise ValueError(f"`{field_name}` must be an integer greater than 0.")
    return resolved


def _is_textual_content_type(content_type: str) -> bool:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if not normalized:
        return False
    if normalized.startswith(_TEXTUAL_CONTENT_TYPES):
        return True
    return normalized.endswith("+json") or normalized.endswith("+xml")


def _looks_like_text(payload: bytes) -> bool:
    if not payload:
        return True
    if b"\x00" in payload:
        return False
    sample = payload[:512]
    printable = sum(byte in {9, 10, 13} or 32 <= byte <= 126 for byte in sample)
    return printable / len(sample) >= 0.85


def _build_request(url: str) -> str | Request:
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        return Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; buducca-fetch-url-skill/1.0)"},
            method="GET",
        )
    return url


def _fetch_url(url: str, *, timeout_seconds: int, max_bytes: int) -> tuple[str, int | None, str, bytes, bool]:
    request = _build_request(url)
    with urlopen(request, timeout=timeout_seconds) as response:
        final_url = getattr(response, "geturl", lambda: url)()
        status = getattr(response, "status", None)
        if status is None:
            getcode = getattr(response, "getcode", None)
            status = getcode() if callable(getcode) else None
        headers = getattr(response, "headers", None)
        content_type = ""
        if headers is not None:
            content_type = headers.get("Content-Type", "")
        payload = response.read(max_bytes + 1)
        truncated = len(payload) > max_bytes
        if truncated:
            payload = payload[:max_bytes]
        return final_url, status, content_type, payload, truncated


def _format_output(
    *,
    final_url: str,
    status: int | None,
    content_type: str,
    payload: bytes,
    truncated: bool,
) -> str:
    lines = [f"URL: {final_url}"]
    if status is not None:
        lines.append(f"Status: {status}")
    lines.append(f"Content-Type: {content_type or 'unknown'}")
    if truncated:
        lines.append("Truncated: yes")

    if _is_textual_content_type(content_type) or (not content_type and _looks_like_text(payload)):
        charset = "utf-8"
        body = payload.decode(charset, errors="replace")
    else:
        lines.append("Content-Transfer-Encoding: base64")
        body = base64.b64encode(payload).decode("ascii")

    lines.append("")
    lines.append(body)
    return "\n".join(lines)


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    del workspace

    url = str(args.get("url", "")).strip()
    if not url:
        return "Missing required arg `url`."

    parsed = urlparse(url)
    if not parsed.scheme:
        return "Invalid arg `url`. A URL scheme is required."

    try:
        timeout_seconds = _resolve_positive_int(
            args.get("timeout_seconds"),
            field_name="timeout_seconds",
            default=_DEFAULT_TIMEOUT_SECONDS,
        )
        max_bytes = _resolve_positive_int(
            args.get("max_bytes"),
            field_name="max_bytes",
            default=_DEFAULT_MAX_BYTES,
        )
    except ValueError as exc:
        return str(exc)

    try:
        final_url, status, content_type, payload, truncated = _fetch_url(
            url,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
    except Exception as exc:
        return f"URL fetch failed: {exc}"

    return _format_output(
        final_url=final_url,
        status=status,
        content_type=content_type,
        payload=payload,
        truncated=truncated,
    )


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    return ActionEnvelope(
        name="fetch_url",
        args=args,
        reason="Download and return the contents of a URL.",
        writes=[],
        requires_approval=False,
    )


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
        "build_action": build_action,
    }
