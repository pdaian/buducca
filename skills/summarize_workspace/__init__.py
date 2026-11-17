from __future__ import annotations

from assistant_framework.workspace import Workspace

NAME = "summarize_workspace"
DESCRIPTION = (
    "Inventory workspace file paths and byte sizes in ascending path order, excluding attachments. "
    "max_items defaults to 20. This does not read or summarize file contents and is not a recent-activity view. "
    "Use file/search_files for content and browse_attachments for inbound files; a limited inventory is not complete."
)
ARGS_SCHEMA = "{ max_items?: number }"


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


def register() -> dict[str, object]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
    }
