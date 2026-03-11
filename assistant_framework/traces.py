from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .workspace import Workspace

TRACE_DIR = "logs/traces"


def write_trace(workspace: Workspace, payload: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    trace_id = f"{stamp}-{uuid4().hex[:8]}"
    path = f"{TRACE_DIR}/{trace_id}.json"
    workspace.write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def latest_trace_path(workspace: Workspace) -> Path | None:
    trace_dir = workspace.resolve(TRACE_DIR)
    if not trace_dir.exists():
        return None
    traces = sorted(trace_dir.glob("*.json"))
    return traces[-1] if traces else None


def load_trace(workspace: Workspace, path: str | None = None) -> dict[str, Any]:
    trace_path = Path(path) if path and str(path).strip() else latest_trace_path(workspace)
    if trace_path is None:
        return {}
    try:
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def replay_trace(trace: dict[str, Any]) -> str:
    if not trace:
        return "No trace found."
    final_reply = trace.get("final_reply")
    if isinstance(final_reply, str) and final_reply.strip():
        return final_reply
    error = trace.get("error")
    if isinstance(error, str) and error.strip():
        return f"Trace ended with error: {error}"
    return "Trace does not contain a final reply."
