from __future__ import annotations

from typing import Any

from assistant_framework.workspace import Workspace

NAME = "learn"
DESCRIPTION = (
    "Append a durable one-line learning to workspace/learnings so future prompts can reuse it."
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

