from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workspace import Workspace


def normalize_collected_item(
    *,
    source: str,
    timestamp: str,
    title: str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "timestamp": timestamp,
        "title": title.strip(),
        "text": text.strip(),
        "attachments": attachments or [],
        "metadata": metadata or {},
    }


def append_normalized_records(workspace: Workspace, collector: str, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    path = f"collected/normalized/{collector}.jsonl"
    workspace.append_text(path, "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n")


def write_raw_snapshot(workspace: Workspace, collector: str, payload: Any) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = f"collected/raw/{collector}/{stamp}.json"
    workspace.write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def ingest_attachment(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path)
    metadata = {"path": str(path), "content_type": path.suffix.lower()}
    try:
        if path.suffix.lower() in {".txt", ".md", ".json", ".csv"}:
            text = path.read_text(encoding="utf-8")
            return {"text": text, "metadata": metadata}
        if path.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore
            except Exception:
                return {"text": "", "metadata": {**metadata, "warning": "pypdf unavailable"}}
            reader = PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return {"text": text, "metadata": metadata}
    except Exception as exc:
        return {"text": "", "metadata": {**metadata, "error": str(exc)}}
    return {"text": "", "metadata": {**metadata, "warning": "unsupported_attachment_type"}}
