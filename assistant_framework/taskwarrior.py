"""Shared, non-interactive Taskwarrior CLI access for harness skills."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

TASK_TIMEOUT_SECONDS = 30
_CLI_FLAGS = [
    "rc.confirmation=off",
    "rc.bulk=0",
    "rc.recurrence.confirmation=no",
    "rc.dependency.confirmation=off",
    "rc.context=",
    "rc.json.array=on",
    "rc.color=off",
    "rc.verbose=nothing",
    "rc.allow.empty.filter=off",
]
_UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class TaskwarriorError(RuntimeError):
    """A CLI failure or an unusable response from Taskwarrior."""


def task_uuid(value: Any) -> str:
    if not isinstance(value, str) or not _UUID_PATTERN.fullmatch(value):
        raise ValueError("Expected a full Taskwarrior UUID.")
    return value.lower()


def run_task(arguments: list[str]) -> str:
    """Use the harness user's Taskwarrior configuration; never invoke a shell."""
    try:
        result = subprocess.run(
            ["task", *_CLI_FLAGS, *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=TASK_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise TaskwarriorError("Taskwarrior CLI not found. Install `task` and ensure it is on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise TaskwarriorError(
            f"Taskwarrior timed out after {TASK_TIMEOUT_SECONDS}s. "
            "A write may already have taken effect; inspect the affected tasks before retrying."
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise TaskwarriorError(f"Taskwarrior command could not complete: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit code {result.returncode}"
        raise TaskwarriorError(f"Taskwarrior command failed: {detail}")
    return (result.stdout or "").strip()


def export_tasks(filters: list[str]) -> list[dict[str, Any]]:
    # Taskwarrior 3.5 treats arguments after export as report names, not filters.
    # Keep the filter in one parenthesized argument before export so command
    # names and aliases within search terms cannot become write operations.
    # limit is a CLI setting, so it must remain outside the grouped expression.
    limits = [term for term in filters if term.startswith(("limit:", "limit="))]
    terms = [term for term in filters if not term.startswith(("limit:", "limit="))]
    arguments = [f"( {' '.join(terms)} )"] if terms else []
    output = run_task([*arguments, *limits, "export"])
    try:
        tasks = json.loads(output)
    except json.JSONDecodeError as exc:
        raise TaskwarriorError("Taskwarrior export returned invalid JSON.") from exc
    if not isinstance(tasks, list) or any(not isinstance(task, dict) for task in tasks):
        raise TaskwarriorError("Taskwarrior export must return a JSON array of task objects.")
    return tasks
