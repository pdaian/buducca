from __future__ import annotations

import subprocess
from typing import Any

from assistant_framework.workspace import Workspace

NAME = "taskwarrior"
DESCRIPTION = (
    "Manage Taskwarrior todos. "
    "Use args.action (or args.command) with one of: list/add/done/modify. "
    "add needs args.description and accepts optional args.project/args.due. "
    "modify needs args.tasks (list of IDs) and supports args.project/args.due updates. "
    "done needs args.tasks (list of IDs), list accepts optional args.filter."
)


def _run_task_command(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return "Taskwarrior CLI not found. Please install `task` and ensure it is in PATH."

    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()

    if result.returncode == 0:
        return output or "Command completed successfully."

    if error:
        return f"Taskwarrior command failed: {error}"
    return f"Taskwarrior command failed with exit code {result.returncode}."


def _build_optional_fields(args: dict[str, Any]) -> list[str]:
    fields: list[str] = []

    if "project" in args:
        project = str(args.get("project", "")).strip()
        fields.append(f"project:{project}")

    if "due" in args:
        due = str(args.get("due", "")).strip()
        if due:
            fields.append(f"due:{due}")

    return fields


def _parse_task_ids(args: dict[str, Any], action: str) -> tuple[list[str], str | None]:
    raw_tasks = args.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        return [], f"Missing required arg `tasks` (non-empty list) for action `{action}`."

    task_ids: list[str] = []
    for task in raw_tasks:
        task_id = str(task).strip()
        if task_id:
            task_ids.append(task_id)

    if not task_ids:
        return [], f"Missing required arg `tasks` (non-empty list) for action `{action}`."

    return task_ids, None


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    _ = workspace
    action_raw = args.get("action")
    if action_raw is None:
        action_raw = args.get("command", "list")
    action = str(action_raw).strip().lower()

    if action == "list":
        command = ["task", "list"]
        raw_filter = args.get("filter")
        if isinstance(raw_filter, str) and raw_filter.strip():
            command.extend(raw_filter.split())
        elif isinstance(raw_filter, list):
            command.extend(str(part) for part in raw_filter)
        return _run_task_command(command)

    if action == "add":
        description = str(args.get("description", "")).strip()
        if not description:
            return "Missing required arg `description` for action `add`."
        command = ["task", "add", *_build_optional_fields(args), description]
        return _run_task_command(command)

    if action == "done":
        task_ids, error = _parse_task_ids(args, action)
        if error:
            return error
        return _run_task_command(["task", *task_ids, "done"])

    if action == "modify":
        task_ids, error = _parse_task_ids(args, action)
        if error:
            return error

        fields = _build_optional_fields(args)
        if not fields:
            return "Action `modify` requires at least one of: `project`, `due`."

        return _run_task_command(["task", *task_ids, "modify", *fields])

    return "Unsupported action. Use one of: list, add, done, modify."
