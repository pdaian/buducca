from __future__ import annotations

import json
import re
import shlex
from typing import Any

from assistant_framework.taskwarrior import TaskwarriorError, export_tasks, run_task, task_uuid
from assistant_framework.workspace import Workspace

NAME = "taskwarrior"
DESCRIPTION = (
    "List, add, modify, or complete todos in the harness user's Taskwarrior database. "
    "action defaults to list; list exports all statuses unless filter is supplied. "
    "Use filter ['status:pending'] for pending todos or ['+OVERDUE'] for overdue todos. "
    "Prefer an array of Taskwarrior filter terms; no shell commands or rc overrides. "
    "Read tasks first and use their full UUIDs in tasks for modify/done; numeric IDs can change. "
    "add requires description. modify requires tasks and at least one of description/project/due. "
    "done requires tasks. Empty project/due clears that field; omitted fields stay unchanged. "
    "due accepts Taskwarrior dates such as 2026-11-20 or tomorrow in the harness timezone. "
    "Descriptions are literal text. JSON results use ok; inspect error on failure. "
    "Inspect affected tasks before retrying a failed write. Use advancetodos to shift existing due dates."
)
ARGS_SCHEMA = """
{
  action?: "list" | "add" | "modify" | "done";
  filter?: string | string[];
  tasks?: Array<string | number>;
  description?: string;
  project?: string;
  due?: string;
}
""".strip()
_ACTION_FIELDS = {
    "list": {"filter"},
    "add": {"description", "project", "due"},
    "modify": {"tasks", "description", "project", "due"},
    "done": {"tasks"},
}


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise ValueError(f"`{name}` must be a string without NUL characters.")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f"`{name}` must be a non-empty string.")
    return value


def _filter_terms(value: Any) -> list[str]:
    if isinstance(value, str):
        terms = shlex.split(value)
    elif isinstance(value, list):
        terms = [_text(term, "filter item") for term in value]
    else:
        raise ValueError("`filter` must be a string or an array of strings.")
    for term in terms:
        if "\0" in term or re.search(r"(?:^|\s)(?:rc[.:=]|--(?:\s|$))", term, re.IGNORECASE):
            raise ValueError("`filter` accepts search terms, not rc overrides or `--`.")
    # shlex removes shell quoting, but Taskwarrior needs quotes inside attribute
    # pairs with spaces. Otherwise project:Deep Work becomes a free-text search.
    normalized: list[str] = []
    for term in terms:
        name, separator, value = term.partition(":")
        if separator and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*", name) and any(char.isspace() for char in value):
            if not (len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"):
                term = f"{name}:{json.dumps(value, ensure_ascii=False)}"
        normalized.append(term)
    return normalized


def _task_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("`tasks` must be a non-empty array of positive IDs or full UUIDs.")
    targets: list[str] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise ValueError("Each task must be a positive integer ID or full UUID.")
        target = str(item).strip()
        if not re.fullmatch(r"[1-9][0-9]*", target):
            target = task_uuid(target)
        if target not in targets:
            targets.append(target)
    return targets


def _modifications(args: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for name in ("project", "due"):
        if name in args:
            value = _text(args[name], name, allow_empty=True)
            # Taskwarrior, as well as the shell, parses attribute values. Keep
            # quotes around non-empty values so spaces remain part of the value.
            fields.append(f"{name}:{json.dumps(value, ensure_ascii=False) if value else ''}")
    if "description" in args:
        fields.extend(["--", _text(args["description"], "description")])
    return fields


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    _ = workspace
    action = "list"
    try:
        if not isinstance(args, dict):
            raise ValueError("Arguments must be a JSON object.")
        action = _text(args.get("action", args.get("command", "list")), "action").lower()
        if "action" in args and "command" in args:
            if _text(args["command"], "command").lower() != action:
                raise ValueError("`action` and legacy `command` must agree when both are supplied.")
        if action not in _ACTION_FIELDS:
            raise ValueError("Unsupported action. Use list, add, modify, or done.")
        unknown = args.keys() - _ACTION_FIELDS[action] - {"action", "command"}
        if unknown:
            raise ValueError(f"Unsupported arguments for {action}: {', '.join(sorted(unknown))}.")
        if action == "list":
            tasks = export_tasks(_filter_terms(args["filter"]) if "filter" in args else [])
            result = {"ok": True, "action": action, "tasks": tasks}
        else:
            targets = _task_ids(args.get("tasks")) if action in {"modify", "done"} else []
            if action == "add" and "description" not in args:
                raise ValueError("`add` requires a non-empty `description`.")
            fields = _modifications(args)
            if action == "modify" and not fields:
                raise ValueError("`modify` requires at least one of description, project, or due.")
            output = run_task([*targets, action, "rc.verbose=new-id,affected", *fields])
            result = {"ok": True, "action": action, "output": output}
            if targets:
                result["targets"] = targets
    except (ValueError, TaskwarriorError) as exc:
        result = {"ok": False, "action": action, "error": str(exc)}
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
        "requires_llm_response": True,
    }
