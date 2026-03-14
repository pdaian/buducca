from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.main_group import MAIN_GROUP_FILE, normalize_backend, write_main_group
from assistant_framework.workspace import Workspace
from messaging_llm_bot.config import ContactConfig, WORKSPACE_CONTACT_MAP_FILES, load_config

NAME = "main_group"
DESCRIPTION = "Save the main group target used for hourly scheduled output. Persists assistant/main_group.json."
ARGS_SCHEMA = """
{
  group?: string;
  name?: string;
  backend?: "telegram" | "signal" | "whatsapp" | "google_fi" | "fi";
  conversation_id?: string | number;
  config_path?: string;
}
""".strip()


def _resolve_config_path(workspace: Workspace, raw_path: Any) -> Path:
    config_path = str(raw_path or "config.json").strip() or "config.json"
    return workspace.resolve(config_path)


def _normalize_contact_recipient(contact: ContactConfig) -> str:
    if contact.platform == "telegram":
        try:
            return str(int(contact.recipient))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Saved telegram contact `{contact.name}` does not have a valid integer chat id.") from exc
    recipient = str(contact.recipient).strip()
    if not recipient:
        raise ValueError(f"Saved contact `{contact.name}` does not have a usable recipient.")
    return recipient


def _workspace_contact_matches(workspace: Workspace, requested_name: str, backend_filter: str) -> list[ContactConfig]:
    matches: list[ContactConfig] = []
    requested_key = requested_name.strip().lower()
    for platform, relative_path in WORKSPACE_CONTACT_MAP_FILES.items():
        if backend_filter and platform != backend_filter:
            continue
        raw = workspace.read_text(relative_path, default="").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for name, recipient in payload.items():
            rendered_name = str(name).strip()
            if rendered_name.lower() != requested_key:
                continue
            matches.append(ContactConfig(name=rendered_name, platform=platform, recipient=recipient))
    return matches


def _resolve_contact(args: dict[str, Any], workspace: Workspace) -> tuple[str, str, str]:
    requested_name = str(args.get("group") or args.get("name") or "").strip()
    if not requested_name:
        raise ValueError("Missing required arg `group` or `name`.")

    backend_filter = str(args.get("backend") or "").strip()
    normalized_filter = normalize_backend(backend_filter) if backend_filter else ""

    matches = _workspace_contact_matches(workspace, requested_name, normalized_filter)
    if not matches:
        config = load_config(_resolve_config_path(workspace, args.get("config_path")))
        matches = [contact for contact in config.contacts if contact.name.strip().lower() == requested_name.lower()]
        if normalized_filter:
            matches = [contact for contact in matches if contact.platform == normalized_filter]
    if not matches:
        raise ValueError(f"No saved contact or group named `{requested_name}` was found.")
    if len(matches) > 1:
        options = ", ".join(sorted({contact.platform for contact in matches}))
        raise ValueError(f"Multiple saved contacts named `{requested_name}` exist ({options}). Pass `backend` to disambiguate.")

    match = matches[0]
    return match.platform, _normalize_contact_recipient(match), requested_name


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    try:
        raw_backend = args.get("backend")
        raw_conversation_id = args.get("conversation_id", args.get("chat_id"))
        if raw_backend not in (None, "") and raw_conversation_id not in (None, ""):
            backend = normalize_backend(raw_backend)
            conversation_id = str(raw_conversation_id)
            name = str(args.get("group") or args.get("name") or "").strip()
        else:
            backend, conversation_id, name = _resolve_contact(args, workspace)
        record = write_main_group(workspace, backend=backend, conversation_id=conversation_id, name=name)
    except ValueError as exc:
        return str(exc)

    label = f" ({record['name']})" if record.get("name") else ""
    return f"Saved main group{label}: {record['backend']}:{record['conversation_id']}"


def build_action(args: dict[str, Any]) -> ActionEnvelope | None:
    return ActionEnvelope(
        name="main_group.write",
        args=args,
        reason="Persist the preferred main group target for hourly scheduled output.",
        writes=[MAIN_GROUP_FILE],
        requires_approval=True,
    )


def register() -> dict[str, Any]:
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "run": run,
        "args_schema": ARGS_SCHEMA,
        "build_action": build_action,
    }
