from __future__ import annotations

import subprocess
from typing import Any

from assistant_framework.workspace import Workspace

NAME = "taskwarrior"
DESCRIPTION = (
    "Manage Taskwarrior todos. "
    "Use args.action (or args.command) with one of: list/add/done. "
    "add needs args.description, done needs args.id, list accepts optional args.filter."
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
        return _run_task_command(["task", "add", description])

    if action == "done":
        task_id = str(args.get("id", "")).strip()
        if not task_id:
            return "Missing required arg `id` for action `done`."
        return _run_task_command(["task", task_id, "done"])

    return "Unsupported action. Use one of: list, add, done."
