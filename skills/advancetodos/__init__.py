from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from assistant_framework.taskwarrior import TaskwarriorError, export_tasks, run_task, task_uuid
from assistant_framework.workspace import Workspace

NAME = "advancetodos"
DESCRIPTION = (
    "Shift Taskwarrior due dates by a nonzero fixed amount added to each existing due date. "
    "Positive amounts move later; negative amounts move earlier. Use weeks, days (24h), hours, minutes (min), "
    "or seconds, e.g. '2d', '1h 30min', '-PT90M'; one leading sign applies to the whole duration. "
    "Months/years and bare m are unsupported. scope defaults to overdue pending/waiting tasks; "
    "choose all only when the request includes future tasks. Both start_date/end_date (YYYY-MM-DD) imply "
    "date_range and select inclusive dates in the harness timezone; do not combine dates with overdue/all. "
    "Recurring templates and completed/deleted/undated tasks are excluded. Only due is explicitly changed. "
    "dry_run=true previews; default false applies. Check JSON ok, matched, updated_count, and old/new dates. "
    "After failure inspect updated/failed UUIDs and report failed.error before retrying; a repeat can shift dates twice."
)
ARGS_SCHEMA = """
{
  amount: string;
  scope?: "overdue" | "all" | "date_range";
  start_date?: string;
  end_date?: string;
  dry_run?: boolean;
}
""".strip()
_OPEN_STATUSES = {"pending", "waiting"}
_OPEN_FILTER = ["(", "status:pending", "or", "status:waiting", ")", "due.any:"]
_UNIT_SECONDS = {
    "w": 604800, "wk": 604800, "wks": 604800, "week": 604800, "weeks": 604800,
    "d": 86400, "day": 86400, "days": 86400,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
}
_DURATION_PART = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([a-z]+)", re.IGNORECASE)
_ISO_DURATION = re.compile(
    r"P(?:(\d+(?:\.\d+)?)W)?(?:(\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?",
    re.IGNORECASE,
)


def _duration(value: Any) -> timedelta:
    error = "`amount` must be a nonzero fixed duration in whole seconds, e.g. '2d', '-1h 30min', or '-PT90M'."
    if not isinstance(value, str) or not value.strip():
        raise ValueError(error)
    value = value.strip()
    sign = 1
    if value[0] in "+-":
        sign = -1 if value[0] == "-" else 1
        value = value[1:].lstrip()
    seconds = Decimal(0)
    if value.upper().startswith("P"):
        match = _ISO_DURATION.fullmatch(value)
        if not match or not any(match.groups()) or value.upper().endswith("T"):
            raise ValueError(error)
        for number, factor in zip(match.groups(), (604800, 86400, 3600, 60, 1)):
            seconds += Decimal(number or "0") * factor
    else:
        position = 0
        while position < len(value):
            match = _DURATION_PART.match(value, position)
            if not match or match[2].lower() not in _UNIT_SECONDS:
                raise ValueError(error)
            seconds += Decimal(match[1]) * _UNIT_SECONDS[match[2].lower()]
            position = match.end()
    if seconds <= 0 or seconds != seconds.to_integral_value():
        raise ValueError(error)
    try:
        return timedelta(seconds=sign * int(seconds))
    except OverflowError as exc:
        raise ValueError("`amount` is too large.") from exc


def _date(value: Any, name: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(f"`{name}` must be a date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"`{name}` must be a valid date in YYYY-MM-DD format.") from exc


def _due(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", value):
        raise ValueError("Taskwarrior returned an invalid UTC due date.")
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _plan(
    tasks: list[dict[str, Any]], amount: timedelta,
    start: date | None, end: date | None, overdue_before: datetime | None,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task.get("status"), str):
            raise ValueError("Taskwarrior returned an invalid task status.")
        if task.get("status") not in _OPEN_STATUSES or not task.get("due"):
            continue
        due = _due(task["due"])
        if overdue_before is not None and due >= overdue_before:
            continue
        if start is not None and end is not None:
            if not start <= due.astimezone().date() <= end:
                continue
        uuid = task_uuid(task.get("uuid"))
        if uuid in seen:
            raise ValueError(f"Taskwarrior exported duplicate UUID {uuid}.")
        seen.add(uuid)
        try:
            new_due = due + amount
        except OverflowError as exc:
            raise ValueError(f"Advancing task {uuid} would exceed the supported date range.") from exc
        changes.append({
            "uuid": uuid,
            "description": task.get("description", ""),
            "old_due": task["due"],
            "new_due": f"{new_due.year:04d}{new_due:%m%dT%H%M%SZ}",
        })
    return changes


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    _ = workspace
    try:
        if not isinstance(args, dict):
            raise ValueError("Arguments must be a JSON object.")
        unknown = args.keys() - {"amount", "scope", "start_date", "end_date", "dry_run"}
        if unknown:
            raise ValueError(f"Unsupported arguments: {', '.join(sorted(unknown))}.")
        amount = _duration(args.get("amount"))
        dry_run = args.get("dry_run", False)
        if not isinstance(dry_run, bool):
            raise ValueError("`dry_run` must be a boolean.")
        if ("start_date" in args) != ("end_date" in args):
            raise ValueError("Supply both `start_date` and `end_date`, or omit both.")
        start = _date(args["start_date"], "start_date") if "start_date" in args else None
        end = _date(args["end_date"], "end_date") if "end_date" in args else None
        if start is not None and end is not None and start > end:
            raise ValueError("`start_date` must be on or before `end_date`.")
        scope = args.get("scope", "date_range" if start is not None else "overdue")
        if scope not in ("overdue", "all", "date_range"):
            raise ValueError("`scope` must be overdue, all, or date_range.")
        if (scope == "date_range") != (start is not None):
            raise ValueError("`start_date` and `end_date` are required only for scope='date_range'.")
        overdue_before = datetime.now(timezone.utc) if scope == "overdue" else None
        changes = _plan(export_tasks(_OPEN_FILTER), amount, start, end, overdue_before)
    except (ValueError, OverflowError, TaskwarriorError) as exc:
        return json.dumps({"ok": False, "error": str(exc), "updated_count": 0})

    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "scope": scope,
        "amount_seconds": amount // timedelta(seconds=1),
        "matched": len(changes),
        "updated_count": 0,
        "updated": [],
    }
    if dry_run:
        result["changes"] = changes
    else:
        for index, change in enumerate(changes):
            try:
                # Match the original due date and open status at write time so
                # an intervening reschedule/completion is not overwritten.
                run_task([
                    change["uuid"], *_OPEN_FILTER, f"due.is:{change['old_due']}",
                    "modify", f"due:{change['new_due']}",
                ])
                actual = export_tasks([change["uuid"]])
                if (
                    len(actual) != 1
                    or actual[0].get("uuid") != change["uuid"]
                    or actual[0].get("due") != change["new_due"]
                ):
                    raise TaskwarriorError("The new due date could not be verified; the task may have changed concurrently.")
            except TaskwarriorError as exc:
                result.update({
                    "ok": False,
                    "error": f"Stopped after a failed update: {exc} Inspect updated/failed tasks before retrying; rerunning can advance tasks twice.",
                    "failed": {**change, "error": str(exc), "outcome": "unknown"},
                    "unattempted_count": len(changes) - index - 1,
                })
                break
            result["updated"].append(change)
            result["updated_count"] += 1
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
        "requires_llm_response": True,
    }
