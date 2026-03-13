from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from assistant_framework.workspace import Workspace

NAME = "read_attachment"
DESCRIPTION = (
    "Read a single file under attachments. "
    "Text files return content; binary files return metadata and optional OCR sidecar text. "
    "Args: path (required attachments/... path), include_ocr_text (optional bool)."
)
ARGS_SCHEMA = '{"path":"required/attachments/...","include_ocr_text":false}'


def _is_attachment_path(path: str) -> bool:
    normalized = path.strip().lstrip("./")
    return normalized == "attachments" or normalized.startswith("attachments/")


def _should_read_as_text(path: Path) -> bool:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is None:
        return path.suffix.lower() in {".txt", ".md", ".json", ".jsonl", ".csv", ".xml", ".html", ".yaml", ".yml"}
    return mime_type.startswith("text/") or mime_type in {
        "application/json",
        "application/xml",
        "application/x-yaml",
    }


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    relative_path = str(args.get("path", "")).strip()
    if not relative_path:
        return "Missing required arg `path`."
    if not _is_attachment_path(relative_path):
        return "`path` must point to a file under attachments/."

    target = workspace.resolve(relative_path)
    if not target.exists():
        return f"{relative_path}: File not found"
    if not target.is_file():
        return f"{relative_path}: Not a file"

    include_ocr_text = bool(args.get("include_ocr_text", False))
    ocr_path = target.parent / f"{target.name}.ocr.txt"
    if not _should_read_as_text(target):
        payload: dict[str, Any] = {
            "path": relative_path,
            "size_bytes": target.stat().st_size,
        }
        if ocr_path.exists():
            payload["ocr_text_path"] = str(ocr_path.relative_to(workspace.root))
            if include_ocr_text:
                payload["ocr_text"] = ocr_path.read_text(encoding="utf-8").strip()
        return json.dumps(payload, ensure_ascii=False, indent=2)

    content = workspace.read_text(relative_path)
    return f"{relative_path}:\n{content}"


def register() -> dict[str, object]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
    }
