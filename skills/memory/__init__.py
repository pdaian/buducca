from __future__ import annotations

import json
from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.memory import delete_record, list_records, read_record, write_record
from assistant_framework.workspace import Workspace

NAME = "memory"
DESCRIPTION = "Validated reads and writes for assistant/people, assistant/tasks, assistant/routines, and assistant/facts."
ARGS_SCHEMA = '{"action":"get|list|upsert|delete","area":"people|tasks|routines|facts","id":"optional","record":{"...":"..."}}'


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    action = str(args.get("action", "get")).strip().lower()
    area = str(args.get("area", "")).strip().lower()

    try:
        if action == "list":
            return json.dumps(list_records(workspace, area), ensure_ascii=False, indent=2)

        entry_id = str(args.get("id", "")).strip()
        if action == "get":
            if not entry_id:
                return "Missing required arg `id`."
            record = read_record(workspace, area, entry_id)
            return json.dumps(record, ensure_ascii=False, indent=2) if record else f"No {area} record found for `{entry_id}`."

        if action == "delete":
            if not entry_id:
                return "Missing required arg `id`."
            deleted = delete_record(workspace, area, entry_id)
            return f"Deleted {area} record `{entry_id}`." if deleted else f"No {area} record found for `{entry_id}`."

        if action == "upsert":
            record = args.get("record", args)
            if not isinstance(record, dict):
                return "Arg `record` must be an object."
            normalized = write_record(workspace, area, record)
            return json.dumps(normalized, ensure_ascii=False, indent=2)
    except ValueError as exc:
        return str(exc)

    return "Unsupported action. Use get, list, upsert, or delete."


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    action = str(args.get("action", "get")).strip().lower()
    area = str(args.get("area", "")).strip().lower()
    entry_id = str(args.get("id", "")).strip() or str((args.get("record") or {}).get("id", "")).strip()
    writes = []
    if action in {"upsert", "delete"} and area:
        target = f"assistant/{area}/{entry_id or '<new>'}.json"
        writes = [target, f"assistant/{area}/history.jsonl"]
    return ActionEnvelope(
        name=f"memory.{action}",
        args=args,
        reason=f"{action} structured memory in assistant/{area}",
        writes=writes,
        requires_approval=action in {"upsert", "delete"},
    )


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
        "build_action": build_action,
    }
