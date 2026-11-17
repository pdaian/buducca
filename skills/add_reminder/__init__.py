from __future__ import annotations

import json
import time
from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.reminders import REMINDERS_FILE, ReminderStore
from assistant_framework.workspace import Workspace

NAME = "add_reminder"
DESCRIPTION = (
    "Schedule prepared reminder messages, or list/update/remove them by stable ID. "
    "operation defaults to add: supply message and one future at (ISO-8601 datetime) or unix_time (epoch seconds). "
    "Seconds round up to the next minute. frequency defaults to once; daily/weekly retain local time in "
    "the IANA timezone, with weekly using the first occurrence's weekday. Chat adds default to the current "
    "conversation and configured timezone; standalone adds require backend/conversation_id and default to UTC. "
    "List takes only operation; remove takes operation/id; update needs id and changed fields, preserving others. "
    "Use returned IDs and inspect list before retrying an uncertain add. An independent service sends "
    "the saved message without an LLM call while the bot runs."
)
ARGS_SCHEMA = """
{
  operation?: "add" | "list" | "update" | "remove";
  id?: string;
  message?: string;
  at?: string;
  unix_time?: number;
  frequency?: "once" | "daily" | "weekly";
  timezone?: string;
  backend?: "telegram" | "signal" | "whatsapp" | "android";
  conversation_id?: string | number;
}
""".strip()
_EDIT_FIELDS = {"message", "at", "unix_time", "frequency", "timezone", "backend", "conversation_id"}


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    store = ReminderStore(workspace)
    operation = args.get("operation", "add")
    try:
        unknown = args.keys() - _EDIT_FIELDS - {"operation", "id"}
        if unknown:
            raise ValueError(f"Unknown reminder argument(s): {', '.join(sorted(unknown))}.")
        if operation == "list":
            if args.keys() - {"operation"}:
                raise ValueError("List accepts only `operation`.")
            return json.dumps({"reminders": store.list()}, ensure_ascii=False)
        if operation in ("update", "remove"):
            reminder_id = args.get("id")
            if not isinstance(reminder_id, str) or not reminder_id.strip():
                raise ValueError("Missing required arg `id`.")
            reminder_id = reminder_id.strip()
            if operation == "remove":
                if args.keys() - {"operation", "id"}:
                    raise ValueError("Remove accepts only `operation` and `id`.")
                store.remove(reminder_id)
                return f"Removed reminder {reminder_id}."
            if not _EDIT_FIELDS.intersection(args):
                raise ValueError("Update requires at least one field to change.")
            record = store.update(reminder_id, args, now=time.time())
        elif operation == "add":
            if "id" in args:
                raise ValueError("IDs are assigned on add; use operation=update to edit an existing reminder.")
            record = store.add(args, now=time.time())
        else:
            raise ValueError("`operation` must be add, list, update, or remove.")
    except ValueError as exc:
        return str(exc)
    return json.dumps({"status": "added" if operation == "add" else "updated", "reminder": record}, ensure_ascii=False)


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    operation = args.get("operation", "add")
    if operation not in ("add", "update", "remove"):
        return None
    return ActionEnvelope(
        name=f"add_reminder.{operation}", args=args,
        reason=f"{operation.capitalize()} a scheduled reminder message.",
        writes=[REMINDERS_FILE], requires_approval=True,
    )


def register() -> dict[str, Any]:
    return {"name": NAME, "description": DESCRIPTION, "args_schema": ARGS_SCHEMA, "run": run, "build_action": build_action}
