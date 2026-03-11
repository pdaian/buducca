from __future__ import annotations

from assistant_framework.workspace import Workspace

NAME = "summarize_workspace"
DESCRIPTION = "Summarize workspace files and their sizes."


def run(workspace: Workspace, args: dict) -> str:
    files = sorted(
        [
            path
            for path in workspace.root.rglob("*")
            if path.is_file() and not path.relative_to(workspace.root).parts[:1] == ("attachments",)
        ]
    )
    if not files:
        return "Workspace is empty."

    max_items = int(args.get("max_items", 20))
    lines = []
    for path in files[:max_items]:
        rel = path.relative_to(workspace.root)
        lines.append(f"- {rel} ({path.stat().st_size} bytes)")
    return "\n".join(lines)
