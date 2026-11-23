from __future__ import annotations

import json
import time
from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.reminders import REMINDERS_FILE, ReminderStore
from assistant_framework.workspace import Workspace

NAME = "schedule_prompt"
DESCRIPTION = (
    "Schedule a future LLM prompt to execute with normal workspace context and skill chains, then send its result. "
    "Use for work to perform later; use add_reminder for a prepared message with no model execution. "
    "operation defaults to add: supply a self-contained prompt and one future at (ISO-8601 datetime) or "
    "unix_time (epoch seconds). Seconds round up to the next minute. frequency defaults to once; daily/weekly "
    "retain local time in the IANA timezone. Chat adds default to the current conversation and configured "
    "timezone; standalone adds require backend/conversation_id and default to UTC. Optional files are workspace "
    "paths read when the prompt runs. List takes only operation; remove takes operation/id; update needs id and "
    "changed fields, preserving others. Use returned IDs and list before retrying an uncertain add. "
    "The bot must be running; scheduled work uses the normal execution limits and action policy."
)
ARGS_SCHEMA = """
{
  operation?: "add" | "list" | "update" | "remove";
  id?: string;
  prompt?: string;
  files?: string | string[];
  at?: string;
  unix_time?: number;
  frequency?: "once" | "daily" | "weekly";
  timezone?: string;
  backend?: "telegram" | "signal" | "whatsapp" | "android";
  conversation_id?: string | number;
}
""".strip()
_EDIT_FIELDS = {"prompt", "files", "at", "unix_time", "frequency", "timezone", "backend", "conversation_id"}


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    store = ReminderStore(workspace)
    operation = args.get("operation", "add")
    try:
        unknown = args.keys() - _EDIT_FIELDS - {"operation", "id"}
        if unknown:
            raise ValueError(f"Unknown scheduled prompt argument(s): {', '.join(sorted(unknown))}.")
        if operation == "list":
            if args.keys() - {"operation"}:
                raise ValueError("List accepts only `operation`.")
            return json.dumps({"prompts": store.list(delivery="prompt")}, ensure_ascii=False)
        if operation in ("update", "remove"):
            reminder_id = args.get("id")
            if not isinstance(reminder_id, str) or not reminder_id.strip():
                raise ValueError("Missing required arg `id`.")
            reminder_id = reminder_id.strip()
            if operation == "remove":
                if args.keys() - {"operation", "id"}:
                    raise ValueError("Remove accepts only `operation` and `id`.")
                store.remove(reminder_id, delivery="prompt")
                return f"Removed scheduled prompt {reminder_id}."
            if not _EDIT_FIELDS.intersection(args):
                raise ValueError("Update requires at least one field to change.")
            record = store.update(reminder_id, args, now=time.time(), delivery="prompt")
        elif operation == "add":
            if "id" in args:
                raise ValueError("IDs are assigned on add; use operation=update to edit an existing prompt.")
            record = store.add(args, now=time.time(), delivery="prompt")
        else:
            raise ValueError("`operation` must be add, list, update, or remove.")
    except ValueError as exc:
        return str(exc)
    return json.dumps({"status": "added" if operation == "add" else "updated", "scheduled_prompt": record}, ensure_ascii=False)


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    operation = args.get("operation", "add")
    if operation not in ("add", "update", "remove"):
        return None
    return ActionEnvelope(
        name=f"schedule_prompt.{operation}", args=args,
        reason=f"{operation.capitalize()} a scheduled LLM prompt.",
        writes=[REMINDERS_FILE], requires_approval=True,
    )


def register() -> dict[str, Any]:
    return {"name": NAME, "description": DESCRIPTION, "args_schema": ARGS_SCHEMA, "run": run, "build_action": build_action}
