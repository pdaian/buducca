from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
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
            text = ""
            try:
                from pypdf import PdfReader  # type: ignore
            except Exception:
                metadata["warning"] = "pypdf unavailable"
            else:
                reader = PdfReader(str(path))
                text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
                if text:
                    return {"text": text, "metadata": metadata}

            ocr_text, ocr_metadata = _ocr_pdf_locally(path)
            if ocr_metadata:
                metadata.update(ocr_metadata)
            return {"text": ocr_text, "metadata": metadata}
    except Exception as exc:
        return {"text": "", "metadata": {**metadata, "error": str(exc)}}
    return {"text": "", "metadata": {**metadata, "warning": "unsupported_attachment_type"}}


def _ocr_pdf_locally(path: Path) -> tuple[str, dict[str, Any]]:
    pdftoppm_bin = which("pdftoppm")
    tesseract_bin = which("tesseract")
    if not pdftoppm_bin or not tesseract_bin:
        missing: list[str] = []
        if not pdftoppm_bin:
            missing.append("pdftoppm")
        if not tesseract_bin:
            missing.append("tesseract")
        return "", {"warning": f"local_pdf_ocr_unavailable:{','.join(missing)}"}

    with tempfile.TemporaryDirectory(prefix="pdf-ocr-") as temp_dir:
        prefix = Path(temp_dir) / "page"
        render = subprocess.run(
            [pdftoppm_bin, "-png", "-r", "200", str(path), str(prefix)],
            capture_output=True,
            text=True,
            check=False,
        )
        if render.returncode != 0:
            stderr = render.stderr.strip() or "pdftoppm failed"
            return "", {"warning": "local_pdf_ocr_render_failed", "render_error": stderr}

        pages = sorted(Path(temp_dir).glob("page-*.png"))
        if not pages:
            return "", {"warning": "local_pdf_ocr_no_pages_rendered"}

        chunks: list[str] = []
        for page in pages:
            proc = subprocess.run(
                [tesseract_bin, str(page), "stdout"],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                chunks.append(proc.stdout.strip())

        if not chunks:
            return "", {"warning": "local_pdf_ocr_no_text"}
        return "\n\n".join(chunks), {"ocr": "tesseract"}
