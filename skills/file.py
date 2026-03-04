from __future__ import annotations

from typing import Any

from assistant_framework.workspace import Workspace

NAME = "file"
DESCRIPTION = (
    "Read and edit files inside the workspace. "
    "Use args.action (or args.command) with one of: read/write/append. "
    "All actions require args.path. write/append require args.content."
)


def _read(workspace: Workspace, path: str) -> str:
    target = workspace.resolve(path)
    if not target.exists():
        return f"File not found: {path}"
    return workspace.read_text(path)


def _write(workspace: Workspace, path: str, content: str) -> str:
    workspace.write_text(path, content)
    return f"Wrote {len(content)} character(s) to {path}."


def _append(workspace: Workspace, path: str, content: str) -> str:
    workspace.append_text(path, content)
    return f"Appended {len(content)} character(s) to {path}."


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    action_raw = args.get("action")
    if action_raw is None:
        action_raw = args.get("command", "read")
    action = str(action_raw).strip().lower()

    path = str(args.get("path", "")).strip()
    if not path:
        return "Missing required arg `path`."

    try:
        if action == "read":
            return _read(workspace, path)

        content = args.get("content")
        if content is None:
            return "Missing required arg `content` for action `write`/`append`."
        content_str = str(content)

        if action == "write":
            return _write(workspace, path, content_str)
        if action == "append":
            return _append(workspace, path, content_str)
    except ValueError as exc:
        return str(exc)

    return "Unsupported action. Use one of: read, write, append."
