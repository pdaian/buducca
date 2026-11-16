from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .workspace import Workspace

REMINDERS_FILE = "assistant/reminders.jsonl"
_REMINDERS_LOCK = threading.RLock()


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


def prepare_message_reminder(
    args: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None, now: float,
) -> dict[str, Any]:
    """Validate a direct message reminder without performing any writes."""
    record = dict(existing or {})
    for field in ("message", "frequency", "timezone", "backend", "conversation_id"):
        if field in args:
            record[field] = args[field]
    message = record.get("message", "")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("Missing required arg `message` (non-empty string).")
    record["message"] = message.strip()
    frequency = record.get("frequency", "once")
    if frequency not in ("once", "daily", "weekly"):
        raise ValueError("`frequency` must be once, daily, or weekly.")
    record["frequency"] = frequency
    zone_name = record.get("timezone", "UTC")
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError("`timezone` must be an IANA timezone such as America/New_York or UTC.") from exc
    record["timezone"] = zone_name
    backend = record.get("backend")
    if backend not in ("telegram", "signal", "whatsapp", "android"):
        raise ValueError("`backend` must be telegram, signal, whatsapp, or android.")
    recipient = record.get("conversation_id")
    if isinstance(recipient, bool) or not isinstance(recipient, (str, int)) or not str(recipient).strip():
        raise ValueError("Missing required arg `conversation_id` (string or integer).")
    recipient = str(recipient).strip()
    if backend == "telegram":
        try:
            recipient = str(int(recipient))
        except ValueError as exc:
            raise ValueError("Telegram `conversation_id` must be an integer chat id.") from exc
    record["conversation_id"] = recipient

    if "at" in args and "unix_time" in args:
        raise ValueError("Pass either `at` or `unix_time`, not both.")
    if "at" in args:
        try:
            if not isinstance(args["at"], str) or not any(separator in args["at"] for separator in ("T", " ")):
                raise ValueError("date and time required")
            at = datetime.fromisoformat(args["at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("`at` must be an ISO-8601 date and time.") from exc
        if at.tzinfo is None:
            local = at.replace(tzinfo=zone, fold=0)
            if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != at:
                raise ValueError("`at` falls in a daylight-saving gap; choose an existing local time.")
            at = local
        scheduled = math.ceil(at.timestamp())
    elif "unix_time" in args:
        scheduled = parse_unix_time(args["unix_time"])
        if scheduled is None:
            raise ValueError("`unix_time` must be integer seconds since Unix epoch.")
    elif existing is None:
        raise ValueError("Pass `at` (ISO-8601 date and time) or `unix_time` for the first occurrence.")
    else:
        scheduled = int(record["unix_time"])

    # Round up so a request with seconds never fires ahead of the requested time.
    scheduled = ((scheduled + 59) // 60) * 60
    try:
        datetime.fromtimestamp(scheduled, zone)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("Reminder time is outside the supported datetime range.") from exc
    if (existing is None or "at" in args or "unix_time" in args) and scheduled <= now:
        raise ValueError("The first occurrence must be in the future.")
    record["unix_time"] = scheduled
    if existing is None or any(field in args for field in ("at", "unix_time", "timezone", "frequency")):
        record["anchor_time"] = scheduled
    record.update(
        id=record.get("id") or uuid4().hex,
        created_at=record.get("created_at") or datetime.fromtimestamp(now, timezone.utc).isoformat(),
        revision=uuid4().hex,
        delivery="message",
    )
    return record


def next_reminder_time(record: Mapping[str, Any], *, after: float) -> int | None:
    frequency = record.get("frequency", "once")
    if frequency == "once":
        return None
    if frequency not in ("daily", "weekly"):
        raise ValueError("Invalid reminder frequency.")
    zone = ZoneInfo(record.get("timezone", "UTC"))
    anchor = datetime.fromtimestamp(int(record.get("anchor_time", record["unix_time"])), zone)
    interval = 1 if frequency == "daily" else 7
    today = datetime.fromtimestamp(after, zone).date()
    steps = max(1, (today - anchor.date()).days // interval)
    while True:
        # Calendar arithmetic keeps the local clock time across DST. A missing
        # local time shifts forward by the gap, without changing the anchor.
        local = (anchor.replace(tzinfo=None) + timedelta(days=steps * interval)).replace(tzinfo=zone, fold=0)
        candidate = int(local.timestamp())
        if candidate > after:
            return candidate
        steps += 1


class ReminderStore:
    """Append-only state shared by skills and schedulers in one bot process.

    Each full record replaces the previous record with its ID. Tombstones remove
    records. Unlike a read/rewrite queue, completion cannot erase a concurrent
    addition or edit. Keep one bot process per workspace, as with chat history.
    """

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def _read(self) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for line in self.workspace.read_text(REMINDERS_FILE).splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
                    raise ValueError("missing ID")
                if record.get("deleted") is True:
                    records.pop(record["id"], None)
                    continue
                if parse_unix_time(record.get("unix_time")) is None:
                    raise ValueError("invalid time")
                if not record.get("backend") or not record.get("conversation_id"):
                    raise ValueError("missing destination")
                if record.get("delivery") == "message":
                    if not isinstance(record.get("message"), str) or not record["message"].strip():
                        raise ValueError("missing message")
                    next_reminder_time(record, after=int(record["unix_time"]))
                elif not record.get("prompt"):
                    raise ValueError("missing prompt")
            except (ValueError, TypeError, OverflowError, OSError, ZoneInfoNotFoundError):
                logging.warning("Skipping malformed reminder entry")
                continue
            records[record["id"]] = record
        return records

    def _append(self, record: Mapping[str, Any]) -> None:
        # Isolate a partial final line after a crash, keeping normal writes JSONL.
        previous = self.workspace.read_text(REMINDERS_FILE)
        prefix = "\n" if previous and not previous.endswith("\n") else ""
        self.workspace.append_text(REMINDERS_FILE, prefix + json.dumps(record, ensure_ascii=False) + "\n")

    def list(self) -> list[dict[str, Any]]:
        with _REMINDERS_LOCK:
            return sorted(self._read().values(), key=lambda record: int(record["unix_time"]))

    def add(self, args: Mapping[str, Any], *, now: float) -> dict[str, Any]:
        record = prepare_message_reminder(args, now=now)
        with _REMINDERS_LOCK:
            self._append(record)
        return record

    def update(self, reminder_id: str, args: Mapping[str, Any], *, now: float) -> dict[str, Any]:
        with _REMINDERS_LOCK:
            existing = self._read().get(reminder_id)
            if existing is None:
                raise ValueError(f"Reminder `{reminder_id}` was not found.")
            if existing.get("delivery") != "message":
                raise ValueError("Legacy prompt reminders can be removed but cannot be edited with add_reminder.")
            record = prepare_message_reminder(args, existing=existing, now=now)
            self._append(record)
            return record

    def remove(self, reminder_id: str) -> None:
        with _REMINDERS_LOCK:
            if reminder_id not in self._read():
                raise ValueError(f"Reminder `{reminder_id}` was not found.")
            self._append({"id": reminder_id, "deleted": True})

    def is_current(self, record: Mapping[str, Any]) -> bool:
        with _REMINDERS_LOCK:
            return self._read().get(record["id"]) == record

    def complete(self, record: Mapping[str, Any], *, now: float) -> None:
        with _REMINDERS_LOCK:
            if self._read().get(record["id"]) != record:
                return
            next_time = next_reminder_time(record, after=max(now, int(record["unix_time"])))
            if next_time is None:
                self._append({"id": record["id"], "deleted": True})
            else:
                self._append(dict(record, unix_time=next_time, revision=uuid4().hex))
