from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .workspace import Workspace

MAIN_GROUP_FILE = "assistant/main_group.json"
_SUPPORTED_BACKENDS = {"telegram", "signal", "whatsapp", "google_fi"}


def normalize_backend(value: Any) -> str:
    backend = str(value or "").strip().lower()
    if backend == "fi":
        backend = "google_fi"
    if backend not in _SUPPORTED_BACKENDS:
        raise ValueError("Unsupported backend. Use telegram, signal, whatsapp, or google_fi.")
    return backend


def normalize_conversation_id(value: Any, *, backend: str) -> str:
    if value is None or isinstance(value, bool):
        raise ValueError("Missing required conversation id.")
    if backend == "telegram":
        try:
            return str(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("Telegram conversation id must be an integer chat id.") from exc
    conversation_id = str(value).strip()
    if not conversation_id:
        raise ValueError("Missing required conversation id.")
    return conversation_id


def read_main_group(workspace: Workspace) -> dict[str, str] | None:
    raw = workspace.read_text(MAIN_GROUP_FILE, default="").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    try:
        backend = normalize_backend(payload.get("backend"))
        conversation_id = normalize_conversation_id(payload.get("conversation_id"), backend=backend)
    except ValueError:
        return None

    record = {
        "backend": backend,
        "conversation_id": conversation_id,
    }
    name = str(payload.get("name", "")).strip()
    if name:
        record["name"] = name
    updated_at = str(payload.get("updated_at", "")).strip()
    if updated_at:
        record["updated_at"] = updated_at
    return record


def write_main_group(
    workspace: Workspace,
    *,
    backend: Any,
    conversation_id: Any,
    name: Any = "",
) -> dict[str, str]:
    normalized_backend = normalize_backend(backend)
    normalized_conversation_id = normalize_conversation_id(conversation_id, backend=normalized_backend)
    record = {
        "backend": normalized_backend,
        "conversation_id": normalized_conversation_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    rendered_name = str(name or "").strip()
    if rendered_name:
        record["name"] = rendered_name
    workspace.write_text(MAIN_GROUP_FILE, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return record
