from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assistant_framework.workspace import Workspace

NAME = "search_attachments_by_filename"
DESCRIPTION = (
    "Find saved attachments by a case-insensitive filename substring, with optional YYYY-MM-DD storage date. "
    "query is literal, not a glob, regex, or content search. Returns JSON items with path/date/size_bytes "
    "in descending path order, excluding OCR sidecars; max_items defaults to 20. "
    "Use read_attachment for content and browse_attachments when the filename is unknown."
)
ARGS_SCHEMA = """
{
  query: string;
  date?: string;
  max_items?: number;
}
""".strip()


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
