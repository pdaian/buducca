from __future__ import annotations

import json
from pathlib import Path

from assistant_framework.workspace import Workspace

NAME = "browse_attachments"
DESCRIPTION = (
    "Discover saved attachments when the filename is unknown. Returns JSON items with path, storage date, "
    "size_bytes, and any OCR sidecar path; it does not read the original content. "
    "date optionally filters a YYYY-MM-DD folder; max_items defaults to 20, ordered by descending path. "
    "include_ocr_text defaults to false and only includes existing sidecars. Use read_attachment for a selected file."
)
ARGS_SCHEMA = """
{
  max_items?: number;
  date?: string;
  include_ocr_text?: boolean;
}
""".strip()


def _iter_attachment_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        [
            path
            for path in root.rglob("*")
            if path.is_file() and not path.name.endswith(".ocr.txt")
        ],
        reverse=True,
    )


def run(workspace: Workspace, args: dict) -> str:
    attachments_root = workspace.resolve("attachments")
    files = _iter_attachment_files(attachments_root)
    if not files:
        return json.dumps({"items": []}, ensure_ascii=False, indent=2)

    date = str(args.get("date", "")).strip()
    if date:
        files = [path for path in files if path.relative_to(attachments_root).parts[:1] == (date,)]

    max_items = int(args.get("max_items", 20))
    include_ocr_text = bool(args.get("include_ocr_text", False))
    items: list[dict[str, object]] = []
    for path in files[:max_items]:
        rel = path.relative_to(workspace.root)
        ocr_path = path.parent / f"{path.name}.ocr.txt"
        item: dict[str, object] = {
            "path": str(rel),
            "date": path.relative_to(attachments_root).parts[0],
            "size_bytes": path.stat().st_size,
        }
        if ocr_path.exists():
            item["ocr_text_path"] = str(ocr_path.relative_to(workspace.root))
            if include_ocr_text:
                item["ocr_text"] = ocr_path.read_text(encoding="utf-8").strip()
        items.append(item)
    return json.dumps({"items": items}, ensure_ascii=False, indent=2)


def register() -> dict[str, object]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
    }
