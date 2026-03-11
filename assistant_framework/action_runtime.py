from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .workspace import Workspace

ACTION_POLICY_FILE = "assistant/action_policy.json"
ACTION_AUDIT_FILE = "audit/actions.jsonl"


@dataclass
class ActionEnvelope:
    name: str
    args: dict[str, Any]
    reason: str
    writes: list[str]
    requires_approval: bool = False


def load_action_policy(workspace: Workspace, path: str = ACTION_POLICY_FILE) -> dict[str, Any]:
    raw = workspace.read_text(path, default="").strip()
    if not raw:
        return {"default": "allow", "actions": {}}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"default": "allow", "actions": {}}
    if not isinstance(payload, dict):
        return {"default": "allow", "actions": {}}
    actions = payload.get("actions")
    return {
        "default": str(payload.get("default", "allow")).strip().lower() or "allow",
        "actions": actions if isinstance(actions, dict) else {},
    }


def decide_action(policy: dict[str, Any], action: ActionEnvelope) -> str:
    decision = str(policy.get("actions", {}).get(action.name, policy.get("default", "allow"))).strip().lower()
    if decision not in {"allow", "deny", "ask"}:
        decision = "allow"
    if action.writes and decision == "allow" and action.requires_approval:
        return "ask"
    return decision


def append_action_audit(
    workspace: Workspace,
    *,
    action: ActionEnvelope,
    decision: str,
    status: str,
    result: str | None = None,
    error: str | None = None,
    path: str = ACTION_AUDIT_FILE,
) -> None:
    payload = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "action": asdict(action),
        "decision": decision,
        "status": status,
        "result": result,
        "error": error,
    }
    workspace.append_text(path, json.dumps(payload, ensure_ascii=False) + "\n")
