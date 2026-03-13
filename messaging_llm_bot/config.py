from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any

from assistant_framework.config_files import load_json_path


@dataclass
class TelegramConfig:
    bot_token: str = ""
    mode: str = "bot"
    api_id: int | None = None
    api_hash: str = ""
    session_path: str = "data/telegram_user"
    poll_interval_seconds: float = 1.0
    long_poll_timeout_seconds: int = 30
    allowed_chat_ids: list[int] = field(default_factory=list)
    process_pending_updates_on_startup: bool = False
    read_only: bool = False
    store_unanswered_messages: bool = False


@dataclass
class SignalConfig:
    account: str
    poll_interval_seconds: float = 1.0
    allowed_sender_ids: list[str] = field(default_factory=list)
    allowed_group_ids_when_sender_not_allowed: list[str] = field(default_factory=list)
    receive_command: list[str] = field(default_factory=list)
    send_command: list[str] = field(default_factory=list)
    read_only: bool = False
    store_unanswered_messages: bool = False


@dataclass
class WhatsAppConfig:
    account: str = "default"
    poll_interval_seconds: float = 1.0
    allowed_sender_ids: list[str] = field(default_factory=list)
    allowed_group_ids_when_sender_not_allowed: list[str] = field(default_factory=list)
    receive_command: list[str] = field(default_factory=list)
    send_command: list[str] = field(default_factory=list)
    read_only: bool = False
    store_unanswered_messages: bool = False


@dataclass
class GoogleFiConfig:
    account: str = "default"
    poll_interval_seconds: float = 1.0
    allowed_sender_ids: list[str] = field(default_factory=list)
    receive_command: list[str] = field(default_factory=list)
    send_command: list[str] = field(default_factory=list)
    read_only: bool = False
    store_unanswered_messages: bool = False


@dataclass
class ContactConfig:
    name: str
    platform: str
    recipient: str | int
    description: str = ""


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    endpoint_path: str = "/chat/completions"
    system_prompt: str = (
        "You are a careful, action-oriented assistant. Prioritize correctness over fluency, "
        "ground important claims in available evidence, ask a targeted clarifying question when "
        "requirements are ambiguous or missing, and finish tasks end-to-end when you have enough "
        "information to act."
    )
    temperature: float = 0.2
    max_tokens: int = 400
    history_messages: int = 8
    system_prompt_timezone: str = "America/New_York"
    file_task_layout_prompt: str = "Use the file skill as the default tool for personal tracking and organization tasks. Keep files under assistant/ grouped by domain (for example assistant/notes/, assistant/lists/, assistant/health/, assistant/finance/, assistant/people/, assistant/travel/). Prefer JSONL for append-only logs, JSON for mutable lists, and Markdown for readable notes."


@dataclass
class RuntimeConfig:
    request_timeout_seconds: float = 30.0
    log_level: str = "INFO"
    debug: bool = False
    workspace_dir: str = "workspace"
    collector_status_file: str = "collector_status.json"
    hourly_file: str = "hourly"
    hourly_status_file: str = "hourly_status.json"
    skills_dir: str = "skills"
    collectors_dir: str = "collectors"
    collector_config_path: str = "config/collectors"
    enable_voice_notes: bool = False
    voice_transcribe_command: list[str] = field(default_factory=list)
    max_reply_chunk_chars: int = 4096
    enable_message_send_skill: bool = False
    contacts_file: str = "assistant/people/contacts.json"
    file_skill_actions: list[str] = field(default_factory=lambda: ["read", "write", "append", "move", "create_dir", "delete_dir"])
    action_policy_file: str = "assistant/action_policy.json"
    enable_reply_citations: bool = True


@dataclass
class BotConfig:
    telegram: TelegramConfig | None = None
    signal: SignalConfig | None = None
    whatsapp: WhatsAppConfig | None = None
    google_fi: GoogleFiConfig | None = None
    contacts: list[ContactConfig] = field(default_factory=list)
    llm: LLMConfig | None = None
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


WORKSPACE_CONTACT_MAP_FILES = {
    "telegram": "telegram.contacts",
    "signal": "signal.contacts",
    "whatsapp": "whatsapp.contacts",
    "google_fi": "google_fi.contacts",
}


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise ValueError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        line = exc.doc.splitlines()[exc.lineno - 1] if exc.doc else ""
        pointer = " " * max(exc.colno - 1, 0) + "^"
        message = (
            f"Invalid JSON in config file {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}\n"
            f"{line}\n"
            f"{pointer}"
        )
        raise ValueError(message) from exc


def _strip_comment_keys(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if not key.startswith("_")}


def _normalize_telegram_config(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = _strip_comment_keys(raw)
    normalized.pop("allowed_sender_ids", None)
    normalized.pop("allowed_group_ids_when_sender_not_allowed", None)
    return normalized


def _load_contacts(raw: Any) -> list[ContactConfig]:
    if not isinstance(raw, list):
        return []

    contacts: list[ContactConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        payload = _strip_comment_keys(item)
        name = str(payload.get("name", "")).strip()
        platform = str(payload.get("platform", "")).strip().lower()
        recipient = payload.get("recipient")
        description = str(payload.get("description", "")).strip()
        if not name or not platform or isinstance(recipient, bool) or recipient is None:
            continue
        if isinstance(recipient, (int, float)):
            recipient_value: str | int = int(recipient)
        else:
            recipient_text = str(recipient).strip()
            if not recipient_text:
                continue
            recipient_value = recipient_text
        contacts.append(
            ContactConfig(
                name=name,
                platform=platform,
                recipient=recipient_value,
                description=description,
            )
        )
    return contacts


def _resolve_workspace_root(runtime: RuntimeConfig, *, config_path: Path) -> Path:
    workspace_root = Path(runtime.workspace_dir)
    if not workspace_root.is_absolute():
        workspace_root = (config_path.parent / workspace_root).resolve()
    return workspace_root


def _load_contacts_from_workspace(runtime: RuntimeConfig, *, config_path: Path) -> list[ContactConfig]:
    contacts_file = runtime.contacts_file.strip()
    if not contacts_file:
        return []

    workspace_root = _resolve_workspace_root(runtime, config_path=config_path)
    contacts_path = Path(contacts_file)
    if not contacts_path.is_absolute():
        contacts_path = workspace_root / contacts_path
    if not contacts_path.exists():
        return []
    return _load_contacts(_read_json(contacts_path))


def _load_contact_map_contacts(raw: Any, *, platform: str) -> list[ContactConfig]:
    if not isinstance(raw, dict):
        return []

    contacts: list[ContactConfig] = []
    for raw_name, raw_value in raw.items():
        name = str(raw_name).strip()
        if not name:
            continue

        description = ""
        recipient = raw_value
        if isinstance(raw_value, dict):
            description = str(raw_value.get("description", "")).strip()
            recipient = raw_value.get("recipient")

        if isinstance(recipient, bool) or recipient is None:
            continue
        if isinstance(recipient, (int, float)):
            recipient_value: str | int = int(recipient)
        else:
            recipient_text = str(recipient).strip()
            if not recipient_text:
                continue
            recipient_value = recipient_text

        contacts.append(
            ContactConfig(
                name=name,
                platform=platform,
                recipient=recipient_value,
                description=description,
            )
        )
    return contacts


def _load_top_level_contact_maps(runtime: RuntimeConfig, *, config_path: Path) -> list[ContactConfig]:
    workspace_root = _resolve_workspace_root(runtime, config_path=config_path)
    contacts: list[ContactConfig] = []
    for platform, relative_path in WORKSPACE_CONTACT_MAP_FILES.items():
        contacts_path = workspace_root / relative_path
        if not contacts_path.exists():
            continue
        contacts.extend(_load_contact_map_contacts(_read_json(contacts_path), platform=platform))
    return contacts


def _dedupe_contacts(contacts: list[ContactConfig]) -> list[ContactConfig]:
    merged: dict[tuple[str, str], ContactConfig] = {}
    order: list[tuple[str, str]] = []
    for contact in contacts:
        key = (contact.platform, contact.name)
        if key not in merged:
            order.append(key)
        merged[key] = contact
    return [merged[key] for key in order]


def _validate(config: BotConfig, *, config_path: Path) -> None:
    if not config.telegram and not config.signal and not config.whatsapp and not config.google_fi:
        raise ValueError("At least one frontend must be configured: telegram, signal, whatsapp, or google_fi")

    if config.telegram:
        mode = config.telegram.mode.strip().lower()
        if mode not in {"bot", "user"}:
            raise ValueError("telegram.mode must be either 'bot' or 'user'")
        config.telegram.mode = mode
        if mode == "bot" and not config.telegram.bot_token.strip():
            raise ValueError("telegram.bot_token must be set when telegram.mode is 'bot'")
        if mode == "user":
            if not config.telegram.api_id:
                raise ValueError("telegram.api_id must be set when telegram.mode is 'user'")
            if not config.telegram.api_hash.strip():
                raise ValueError("telegram.api_hash must be set when telegram.mode is 'user'")
            if not config.telegram.session_path.strip():
                raise ValueError("telegram.session_path must be set when telegram.mode is 'user'")
        if config.telegram.poll_interval_seconds < 0:
            raise ValueError("telegram.poll_interval_seconds must be >= 0")
        if config.telegram.long_poll_timeout_seconds <= 0:
            raise ValueError("telegram.long_poll_timeout_seconds must be > 0")

    if config.signal:
        if not config.signal.account.strip():
            raise ValueError("signal.account must be set")
        if config.signal.poll_interval_seconds < 0:
            raise ValueError("signal.poll_interval_seconds must be >= 0")

    if config.whatsapp:
        if not config.whatsapp.account.strip():
            raise ValueError("whatsapp.account must be set")
        if config.whatsapp.poll_interval_seconds < 0:
            raise ValueError("whatsapp.poll_interval_seconds must be >= 0")

    if config.google_fi:
        if not config.google_fi.account.strip():
            raise ValueError("google_fi.account must be set")
        if config.google_fi.poll_interval_seconds < 0:
            raise ValueError("google_fi.poll_interval_seconds must be >= 0")

    valid_contact_platforms = {"telegram", "signal", "whatsapp", "google_fi", "fi"}
    for contact in config.contacts:
        if contact.platform not in valid_contact_platforms:
            raise ValueError(
                "contacts[].platform must be one of: telegram, signal, whatsapp, google_fi, fi"
            )

    if not config.llm:
        raise ValueError("llm must be set")
    if not config.llm.base_url.strip():
        raise ValueError("llm.base_url must be set")
    if not config.llm.api_key.strip():
        raise ValueError("llm.api_key must be set")
    if not config.llm.model.strip():
        raise ValueError("llm.model must be set")
    if config.llm.history_messages < 0:
        raise ValueError("llm.history_messages must be >= 0")
    if not config.llm.system_prompt_timezone.strip():
        raise ValueError("llm.system_prompt_timezone must be set")
    try:
        ZoneInfo(config.llm.system_prompt_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("llm.system_prompt_timezone must be a valid IANA timezone") from exc
    if config.runtime.request_timeout_seconds <= 0:
        raise ValueError("runtime.request_timeout_seconds must be > 0")
    if not config.runtime.hourly_file.strip():
        raise ValueError("runtime.hourly_file must be set")
    if not config.runtime.hourly_status_file.strip():
        raise ValueError("runtime.hourly_status_file must be set")
    if config.runtime.enable_voice_notes and not config.runtime.voice_transcribe_command:
        raise ValueError("runtime.voice_transcribe_command must be set when runtime.enable_voice_notes is true")
    if config.runtime.max_reply_chunk_chars <= 0:
        raise ValueError("runtime.max_reply_chunk_chars must be > 0")
    if not config.runtime.contacts_file.strip():
        raise ValueError("runtime.contacts_file must be set")
    if not config.runtime.file_skill_actions:
        raise ValueError("runtime.file_skill_actions must contain at least one action")

    _ = config_path


def load_config(path: str | Path) -> BotConfig:
    config_path = Path(path)
    raw = load_json_path(config_path)
    if not isinstance(raw, dict):
        raise ValueError(f"Config root must be a JSON object: {config_path}")

    telegram_raw = raw.get("telegram")
    signal_raw = raw.get("signal")
    whatsapp_raw = raw.get("whatsapp")
    google_fi_raw = raw.get("google_fi")
    telegram = TelegramConfig(**_normalize_telegram_config(telegram_raw)) if isinstance(telegram_raw, dict) else None
    signal = SignalConfig(**_strip_comment_keys(signal_raw)) if isinstance(signal_raw, dict) else None
    whatsapp = WhatsAppConfig(**_strip_comment_keys(whatsapp_raw)) if isinstance(whatsapp_raw, dict) else None
    google_fi = GoogleFiConfig(**_strip_comment_keys(google_fi_raw)) if isinstance(google_fi_raw, dict) else None
    try:
        llm = LLMConfig(**_strip_comment_keys(raw["llm"]))
    except KeyError as exc:
        raise ValueError("Missing required top-level section: llm") from exc
    runtime_raw = raw.get("runtime", {})
    runtime = RuntimeConfig(**_strip_comment_keys(runtime_raw if isinstance(runtime_raw, dict) else {}))
    contacts = _dedupe_contacts(
        _load_contacts_from_workspace(runtime, config_path=config_path)
        + _load_top_level_contact_maps(runtime, config_path=config_path)
    )

    config = BotConfig(
        telegram=telegram,
        signal=signal,
        whatsapp=whatsapp,
        google_fi=google_fi,
        contacts=contacts,
        llm=llm,
        runtime=runtime,
    )
    _validate(config, config_path=config_path)
    return config
