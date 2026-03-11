from __future__ import annotations

from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.memory import read_record, write_record
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


def _learning_id(learning: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in learning).strip("-")
    token = "-".join(part for part in token.split("-") if part)
    return f"learn-{token[:48] or 'entry'}"


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    learning = _extract_learning(args)
    if not learning:
        return "Missing required arg `learning` (string)."

    record_id = _learning_id(learning)
    existing = read_record(workspace, "facts", record_id)
    record = {
        "id": record_id,
        "statement": learning,
        "source": "learn",
        "confidence": "user-provided",
    }
    if not existing:
        write_record(workspace, "facts", record)
    workspace.append_text(_LEARNINGS_FILE, f"{learning}\n")
    return f"Learned: {learning}"


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    learning = _extract_learning(args)
    if not learning:
        return None
    record_id = _learning_id(learning)
    return ActionEnvelope(
        name="learn.append",
        args=args,
        reason="Persist a durable fact for future prompts.",
        writes=["learnings", f"assistant/facts/{record_id}.json", "assistant/facts/history.jsonl"],
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
