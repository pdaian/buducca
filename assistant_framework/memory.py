from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .workspace import Workspace

MEMORY_AREAS = ("people", "tasks", "routines", "facts")


def ensure_memory_layout(workspace: Workspace) -> None:
    workspace.create_dir("assistant")
    for area in MEMORY_AREAS:
        workspace.create_dir(f"assistant/{area}")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "entry"


def _memory_path(area: str, entry_id: str) -> str:
    return f"assistant/{area}/{entry_id}.json"


def _history_path(area: str) -> str:
    return f"assistant/{area}/history.jsonl"


def validate_area(area: str) -> str:
    normalized = area.strip().lower()
    if normalized not in MEMORY_AREAS:
        raise ValueError(f"Unsupported memory area: {area}")
    return normalized


def _normalize_iso(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Missing required field `{field_name}`.")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Field `{field_name}` must be an ISO-8601 datetime.") from exc
    return text


def _normalize_id(area: str, payload: dict[str, Any]) -> str:
    raw_id = str(payload.get("id", "")).strip()
    if raw_id:
        return raw_id
    for key in ("name", "title", "subject", "statement"):
        candidate = str(payload.get(key, "")).strip()
        if candidate:
            return _slugify(candidate)
    raise ValueError(f"Missing required field `id` for area `{area}`.")


def _validate_schedule(schedule: Any) -> dict[str, Any]:
    if not isinstance(schedule, dict):
        raise ValueError("Field `schedule` must be an object.")
    frequency = str(schedule.get("frequency", "")).strip().lower()
    if frequency not in {"hourly", "daily", "weekly"}:
        raise ValueError("Field `schedule.frequency` must be one of hourly, daily, weekly.")
    normalized = {"frequency": frequency, "interval": int(schedule.get("interval", 1))}
    if normalized["interval"] <= 0:
        raise ValueError("Field `schedule.interval` must be greater than 0.")
    if frequency in {"daily", "weekly"}:
        normalized["hour"] = int(schedule.get("hour", 9))
        normalized["minute"] = int(schedule.get("minute", 0))
    if frequency == "weekly":
        normalized["weekday"] = int(schedule.get("weekday", 0))
    timezone_name = str(schedule.get("timezone", "UTC")).strip() or "UTC"
    ZoneInfo(timezone_name)
    normalized["timezone"] = timezone_name
    return normalized


def validate_record(area: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_area = validate_area(area)
    normalized = dict(payload)
    normalized["id"] = _normalize_id(normalized_area, normalized)

    if normalized_area == "people":
        name = str(normalized.get("name", "")).strip()
        if not name:
            raise ValueError("Field `name` is required for people.")
        normalized["name"] = name
        normalized["notes"] = str(normalized.get("notes", "")).strip()
        normalized["contact"] = str(normalized.get("contact", "")).strip()
        return normalized

    if normalized_area == "facts":
        statement = str(normalized.get("statement", normalized.get("fact", ""))).strip()
        if not statement:
            raise ValueError("Field `statement` is required for facts.")
        normalized["statement"] = statement
        normalized["source"] = str(normalized.get("source", "memory")).strip() or "memory"
        normalized["confidence"] = str(normalized.get("confidence", "confirmed")).strip() or "confirmed"
        return normalized

    if normalized_area == "tasks":
        title = str(normalized.get("title", "")).strip()
        if not title:
            raise ValueError("Field `title` is required for tasks.")
        normalized["title"] = title
        normalized["status"] = str(normalized.get("status", "open")).strip().lower() or "open"
        due_at = normalized.get("due_at")
        remind_at = normalized.get("remind_at")
        if due_at:
            normalized["due_at"] = _normalize_iso(due_at, "due_at")
        if remind_at:
            normalized["remind_at"] = _normalize_iso(remind_at, "remind_at")
        normalized["kind"] = str(normalized.get("kind", "task")).strip().lower() or "task"
        normalized["details"] = str(normalized.get("details", "")).strip()
        normalized["notify_target"] = dict(normalized.get("notify_target", {})) if isinstance(normalized.get("notify_target"), dict) else {}
        return normalized

    title = str(normalized.get("title", "")).strip()
    if not title:
        raise ValueError("Field `title` is required for routines.")
    normalized["title"] = title
    normalized["enabled"] = bool(normalized.get("enabled", True))
    normalized["instructions"] = str(normalized.get("instructions", "")).strip()
    normalized["schedule"] = _validate_schedule(normalized.get("schedule"))
    if normalized.get("next_run_at"):
        normalized["next_run_at"] = _normalize_iso(normalized["next_run_at"], "next_run_at")
    else:
        normalized["next_run_at"] = calculate_next_run(normalized["schedule"])
    return normalized


def write_record(
    workspace: Workspace,
    area: str,
    payload: dict[str, Any],
    *,
    event: str = "upsert",
) -> dict[str, Any]:
    ensure_memory_layout(workspace)
    normalized_area = validate_area(area)
    normalized = validate_record(normalized_area, payload)
    path = _memory_path(normalized_area, normalized["id"])
    workspace.write_text(path, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    history_entry = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "id": normalized["id"],
        "path": path,
        "record": normalized,
    }
    workspace.append_text(_history_path(normalized_area), json.dumps(history_entry, ensure_ascii=False) + "\n")
    return normalized


def read_record(workspace: Workspace, area: str, entry_id: str) -> dict[str, Any] | None:
    normalized_area = validate_area(area)
    raw = workspace.read_text(_memory_path(normalized_area, entry_id), default="").strip()
    if not raw:
        return None
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else None


def list_records(workspace: Workspace, area: str) -> list[dict[str, Any]]:
    normalized_area = validate_area(area)
    root = workspace.resolve(f"assistant/{normalized_area}")
    if not root.exists():
        return []
    records: list[dict[str, Any]] = []
    for file_path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def delete_record(workspace: Workspace, area: str, entry_id: str) -> bool:
    normalized_area = validate_area(area)
    path = workspace.resolve(_memory_path(normalized_area, entry_id))
    if not path.exists():
        return False
    path.unlink()
    workspace.append_text(
        _history_path(normalized_area),
        json.dumps(
            {
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "event": "delete",
                "id": entry_id,
                "path": _memory_path(normalized_area, entry_id),
            },
            ensure_ascii=False,
        )
        + "\n",
    )
    return True


def calculate_next_run(schedule: dict[str, Any], *, last_run_at: str | None = None, now: datetime | None = None) -> str:
    reference = now or datetime.now(timezone.utc)
    timezone_name = schedule["timezone"]
    local_zone = ZoneInfo(timezone_name)
    local_now = reference.astimezone(local_zone)

    if last_run_at:
        try:
            local_now = max(local_now, datetime.fromisoformat(last_run_at.replace("Z", "+00:00")).astimezone(local_zone))
        except ValueError:
            pass

    frequency = schedule["frequency"]
    interval = int(schedule["interval"])
    if frequency == "hourly":
        candidate = local_now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=interval)
    elif frequency == "daily":
        candidate = local_now.replace(hour=int(schedule["hour"]), minute=int(schedule["minute"]), second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=interval)
    else:
        days_ahead = (int(schedule["weekday"]) - local_now.weekday()) % 7
        candidate = local_now.replace(hour=int(schedule["hour"]), minute=int(schedule["minute"]), second=0, microsecond=0) + timedelta(days=days_ahead)
        if candidate <= local_now:
            candidate += timedelta(days=7 * interval if days_ahead == 0 else 0)
        while candidate <= local_now:
            candidate += timedelta(days=7 * interval)
    return candidate.astimezone(timezone.utc).isoformat()


def mark_task_notified(workspace: Workspace, record: dict[str, Any], *, fired_at: datetime | None = None) -> dict[str, Any]:
    updated = dict(record)
    updated["last_notified_at"] = (fired_at or datetime.now(timezone.utc)).isoformat()
    if updated.get("kind") == "reminder":
        updated["status"] = "done"
    return write_record(workspace, "tasks", updated, event="notify")


def mark_routine_run(workspace: Workspace, record: dict[str, Any], *, ran_at: datetime | None = None) -> dict[str, Any]:
    executed_at = ran_at or datetime.now(timezone.utc)
    updated = dict(record)
    updated["last_run_at"] = executed_at.isoformat()
    updated["next_run_at"] = calculate_next_run(updated["schedule"], last_run_at=updated["last_run_at"], now=executed_at)
    return write_record(workspace, "routines", updated, event="run")
