from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

REMINDERS_FILE = "assistant/reminders.jsonl"


def parse_unix_time(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def normalize_paths(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return []

    seen: set[str] = set()
    paths: list[str] = []
    for item in items:
        path = str(item).strip()
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def create_reminder_record(
    args: Mapping[str, Any],
    *,
    created_at: datetime | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    unix_time = parse_unix_time(args.get("unix_time", args.get("timestamp", args.get("when"))))
    if unix_time is None:
        return None, "Missing required arg `unix_time` (integer seconds since Unix epoch)."

    prompt = str(args.get("prompt", args.get("text", args.get("message", "")))).strip()
    if not prompt:
        return None, "Missing required arg `prompt` (string)."

    backend = str(args.get("backend", "")).strip().lower()
    if not backend:
        return None, "Missing required arg `backend` (string)."

    conversation_raw = args.get("conversation_id", args.get("chat_id"))
    conversation_id = str(conversation_raw).strip() if conversation_raw is not None else ""
    if not conversation_id:
        return None, "Missing required arg `conversation_id` (string)."

    now = created_at or datetime.now(timezone.utc)
    sender_raw = args.get("sender_id")
    sender_id = str(sender_raw).strip() if sender_raw is not None else conversation_id

    record: dict[str, Any] = {
        "id": uuid4().hex,
        "created_at": now.isoformat(),
        "unix_time": unix_time,
        "backend": backend,
        "conversation_id": conversation_id,
        "sender_id": sender_id or conversation_id,
        "prompt": prompt,
        "files": normalize_paths(args.get("files")),
    }
    return record, None


def serialize_reminder_record(record: Mapping[str, Any]) -> str:
    files = normalize_paths(record.get("files"))
    normalized = {
        "id": str(record.get("id", "")).strip(),
        "created_at": str(record.get("created_at", "")).strip(),
        "unix_time": int(record["unix_time"]),
        "backend": str(record["backend"]).strip(),
        "conversation_id": str(record["conversation_id"]).strip(),
        "sender_id": str(record.get("sender_id", record["conversation_id"])).strip(),
        "prompt": str(record["prompt"]).strip(),
        "files": files,
    }
    return json.dumps(normalized, ensure_ascii=False)
