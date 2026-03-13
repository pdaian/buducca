from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assistant_framework.workspace import Workspace

NAME = "search_attachments_by_date"
DESCRIPTION = (
    "List files under attachments for a specific date folder. "
    "Args: date (required YYYY-MM-DD), max_items (optional, default 20)."
)
ARGS_SCHEMA = '{"date":"required/YYYY-MM-DD","max_items":20}'


def _iter_attachment_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        [path for path in root.rglob("*") if path.is_file() and not path.name.endswith(".ocr.txt")],
        reverse=True,
    )


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    date = str(args.get("date", "")).strip()
    if not date:
        return "Missing required arg `date`."

    attachments_root = workspace.resolve("attachments")
    files = [
        path
        for path in _iter_attachment_files(attachments_root)
        if path.relative_to(attachments_root).parts[:1] == (date,)
    ]

    max_items = int(args.get("max_items", 20))
    items = [
        {
            "path": str(path.relative_to(workspace.root)),
            "date": date,
            "size_bytes": path.stat().st_size,
        }
        for path in files[:max_items]
    ]
    return json.dumps({"items": items}, ensure_ascii=False, indent=2)


def register() -> dict[str, object]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
    }
