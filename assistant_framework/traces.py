from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .workspace import Workspace

TRACE_DIR = "traces"
LEGACY_TRACE_DIR = "logs/traces"


def _trace_dir(workspace: Workspace) -> Path:
    return workspace.data_root() / TRACE_DIR


def _resolve_trace_path(workspace: Workspace, path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate

    legacy_path = workspace.resolve(path)
    if legacy_path.exists():
        return legacy_path
    return (_trace_dir(workspace) / path).resolve()


def write_trace(workspace: Workspace, payload: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    trace_id = f"{stamp}-{uuid4().hex[:8]}"
    path = _trace_dir(workspace) / f"{trace_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def latest_trace_path(workspace: Workspace) -> Path | None:
    for trace_dir in (_trace_dir(workspace), workspace.resolve(LEGACY_TRACE_DIR)):
        if not trace_dir.exists():
            continue
        traces = sorted(trace_dir.glob("*.json"))
        if traces:
            return traces[-1]
    return None


def load_trace(workspace: Workspace, path: str | None = None) -> dict[str, Any]:
    trace_path = _resolve_trace_path(workspace, str(path).strip()) if path and str(path).strip() else latest_trace_path(workspace)
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
