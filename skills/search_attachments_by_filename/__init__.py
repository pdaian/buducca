from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assistant_framework.workspace import Workspace

NAME = "search_attachments_by_filename"
DESCRIPTION = (
    "Search files under attachments by filename. "
    "Args: query (required substring match), date (optional YYYY-MM-DD), max_items (optional, default 20)."
)
ARGS_SCHEMA = '{"query":"required","date":"optional/YYYY-MM-DD","max_items":20}'


def _iter_attachment_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        [path for path in root.rglob("*") if path.is_file() and not path.name.endswith(".ocr.txt")],
        reverse=True,
    )


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip().lower()
    if not query:
        return "Missing required arg `query`."

    attachments_root = workspace.resolve("attachments")
    files = _iter_attachment_files(attachments_root)

    date = str(args.get("date", "")).strip()
    if date:
        files = [path for path in files if path.relative_to(attachments_root).parts[:1] == (date,)]

    matches = [path for path in files if query in path.name.lower()]
    max_items = int(args.get("max_items", 20))
    items = [
        {
            "path": str(path.relative_to(workspace.root)),
            "date": path.relative_to(attachments_root).parts[0],
            "size_bytes": path.stat().st_size,
        }
        for path in matches[:max_items]
    ]
    return json.dumps({"items": items}, ensure_ascii=False, indent=2)


def register() -> dict[str, object]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
    }
