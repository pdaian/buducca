from __future__ import annotations

import json
import subprocess
from typing import Any

from assistant_framework.workspace import Workspace

_TASK_TIMEOUT_SECONDS = 30
_NON_INTERACTIVE_FLAGS = [
    "rc.confirmation=off",
    "rc.bulk=1000",
    "rc.recurrence.confirmation=off",
    "rc.dependency.confirmation=off",
]

NAME = "taskwarrior"
DESCRIPTION = (
    "Manage Taskwarrior todos. "
    "Use args.action (or args.command) with one of: list/add/done/modify. "
    "add needs args.description and accepts optional args.project/args.due. "
    "modify needs args.tasks (list of IDs) and supports args.project/args.due updates. "
    "done needs args.tasks (list of IDs), list accepts optional args.filter and returns JSON."
)


def _run_task_command(command: list[str]) -> str:
    full_command = ["task", *_NON_INTERACTIVE_FLAGS, *command[1:]] if command and command[0] == "task" else command
    try:
        result = subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_TASK_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return "Taskwarrior CLI not found. Please install `task` and ensure it is in PATH."
    except subprocess.TimeoutExpired:
        return (
            "Taskwarrior command timed out after "
            f"{_TASK_TIMEOUT_SECONDS}s. It may be waiting for interactive input."
        )

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


def _build_filter_terms(raw_filter: Any) -> list[str]:
    if isinstance(raw_filter, str) and raw_filter.strip():
        return raw_filter.split()
    if isinstance(raw_filter, list):
        return [str(part).strip() for part in raw_filter if str(part).strip()]
    return []


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
        command = ["task", *_build_filter_terms(args.get("filter")), "export"]
        output = _run_task_command(command)
        if output.startswith("Taskwarrior command ") or output in {
            "Taskwarrior CLI not found. Please install `task` and ensure it is in PATH.",
            "Command completed successfully.",
        }:
            return output
        try:
            tasks = json.loads(output)
        except json.JSONDecodeError:
            return "Taskwarrior command returned invalid JSON."
        return json.dumps({"tasks": tasks}, sort_keys=True)

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


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
    }
