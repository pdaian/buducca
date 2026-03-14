from __future__ import annotations

from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.workspace import Workspace

NAME = "learn"
DESCRIPTION = (
    "Save a durable one-line general learning to the workspace learnings file so future prompts can reuse it."
)
ARGS_SCHEMA = "{ learning: string }"

_LEARNINGS_FILE = "learnings"


def _extract_learning(args: dict[str, Any]) -> str:
    for key in ("learning", "text", "line", "message"):
        value = args.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return " ".join(text.splitlines())
    return ""


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    learning = _extract_learning(args)
    if not learning:
        return "Missing required arg `learning` (string)."

    workspace.append_text(_LEARNINGS_FILE, f"{learning}\n")
    return f"Learned: {learning}"


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    learning = _extract_learning(args)
    if not learning:
        return None
    return ActionEnvelope(
        name="learn.append",
        args=args,
        reason="Persist a durable general learning for future prompts.",
        writes=["learnings"],
        requires_approval=True,
    )


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
        "build_action": build_action,
    }
