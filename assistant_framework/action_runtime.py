from __future__ import annotations

import json
import logging
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
    try:
        if not workspace.resolve(path).exists():
            return {"default": "allow", "actions": {}, "allow_all": True}
        payload = json.loads(workspace.read_text(path))
        if not isinstance(payload, dict):
            raise ValueError("expected an object")
        if any(key not in {"default", "actions"} and not key.startswith("_") for key in payload):
            raise ValueError("unknown policy setting")
        default = payload.get("default", "allow")
        actions = payload.get("actions", {})
        if not isinstance(actions, dict):
            raise ValueError("actions must be an object")
        decisions = [default, *actions.values()]
        if any(not isinstance(value, str) or value.strip().lower() not in {"allow", "deny", "ask"} for value in decisions):
            raise ValueError("decisions must be allow, deny, or ask")
    except (ValueError, OSError):
        logging.error("Invalid or unreadable action policy at %s; actions are denied until it is corrected", path)
        return {"default": "deny", "actions": {}, "allow_all": False}
    return {
        "default": default.strip().lower(),
        "actions": {name: decision.strip().lower() for name, decision in actions.items()},
        "allow_all": False,
    }


def decide_action(policy: dict[str, Any], action: ActionEnvelope) -> str:
    decision = str(policy.get("actions", {}).get(action.name, policy.get("default", "allow"))).strip().lower()
    if decision not in {"allow", "deny", "ask"}:
        decision = "deny"
    if action.writes and decision == "allow" and action.requires_approval and not policy.get("allow_all", False):
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
