from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any


@dataclass
class TelegramConfig:
    bot_token: str
    poll_interval_seconds: float = 1.0
    long_poll_timeout_seconds: int = 30
    allowed_chat_ids: list[int] = field(default_factory=list)
    process_pending_updates_on_startup: bool = False


@dataclass
class SignalConfig:
    account: str
    poll_interval_seconds: float = 1.0
    allowed_sender_ids: list[str] = field(default_factory=list)
    receive_command: list[str] = field(default_factory=list)
    send_command: list[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    endpoint_path: str = "/chat/completions"
    system_prompt: str = "You are a helpful assistant."
    temperature: float = 0.2
    max_tokens: int = 400
    history_messages: int = 8
    system_prompt_timezone: str = "America/New_York"


@dataclass
class RuntimeConfig:
    request_timeout_seconds: float = 30.0
    log_level: str = "INFO"
    debug: bool = False
    workspace_dir: str = "workspace"
    collector_status_file: str = "collector_status.json"
    skills_dir: str = "skills"
    collectors_dir: str = "collectors"
    enable_voice_notes: bool = False
    voice_transcribe_command: list[str] = field(default_factory=list)
    max_reply_chunk_chars: int = 4096


@dataclass
class BotConfig:
    telegram: TelegramConfig | None = None
    signal: SignalConfig | None = None
    llm: LLMConfig | None = None
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_token(raw: Any) -> str:
    return str(raw or "").strip()


def _resolve_collector_bot_token(account_cfg: dict[str, Any], default_bot_token: str) -> str:
    return (
        _normalize_token(account_cfg.get("collector_bot_token"))
        or _normalize_token(account_cfg.get("bot_token"))
        or _normalize_token(default_bot_token)
    )


def _validate_telegram_collector_token_ownership(config: BotConfig, *, config_path: Path) -> None:
    if not config.telegram:
        return

    frontend_token = _normalize_token(config.telegram.bot_token)
    if not frontend_token:
        return

    collector_config_path = config_path.parent / "agent_config.json"
    if not collector_config_path.exists():
        return

    collector_raw = _read_json(collector_config_path)
    collectors_config = collector_raw.get("collectors")
    if not isinstance(collectors_config, dict):
        return

    telegram_recent_config = collectors_config.get("telegram_recent")
    if not isinstance(telegram_recent_config, dict):
        legacy = collectors_config.get("telegram_recent_collector")
        telegram_recent_config = legacy if isinstance(legacy, dict) else None
    if not isinstance(telegram_recent_config, dict):
        return

    default_bot_token = (
        _normalize_token(telegram_recent_config.get("collector_bot_token"))
        or _normalize_token(telegram_recent_config.get("bot_token"))
    )

    accounts = telegram_recent_config.get("accounts")
    accounts_cfg = accounts if isinstance(accounts, list) else []
    if not accounts_cfg:
        accounts_cfg = [{"name": telegram_recent_config.get("account_name", "default"), **telegram_recent_config}]

    for account_cfg in accounts_cfg:
        if not isinstance(account_cfg, dict):
            continue
        account_name = str(account_cfg.get("name") or "default")
        effective_bot_token = _resolve_collector_bot_token(account_cfg, default_bot_token)
        if effective_bot_token and effective_bot_token == frontend_token:
            raise ValueError(
                "Invalid Telegram token setup: telegram.bot_token in frontend config matches "
                f"collectors.telegram_recent.accounts[{account_name!r}] collector_bot_token/bot_token. "
                "Only one getUpdates consumer may own a token. "
                "Use user_client.enabled=true or a separate collector_bot_token for the collector."
            )


def _validate(config: BotConfig, *, config_path: Path) -> None:
    if not config.telegram and not config.signal:
        raise ValueError("At least one frontend must be configured: telegram or signal")

    if config.telegram:
        if not config.telegram.bot_token.strip():
            raise ValueError("telegram.bot_token must be set")
        if config.telegram.poll_interval_seconds < 0:
            raise ValueError("telegram.poll_interval_seconds must be >= 0")
        if config.telegram.long_poll_timeout_seconds <= 0:
            raise ValueError("telegram.long_poll_timeout_seconds must be > 0")

    if config.signal:
        if not config.signal.account.strip():
            raise ValueError("signal.account must be set")
        if config.signal.poll_interval_seconds < 0:
            raise ValueError("signal.poll_interval_seconds must be >= 0")

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
    if config.runtime.enable_voice_notes and not config.runtime.voice_transcribe_command:
        raise ValueError("runtime.voice_transcribe_command must be set when runtime.enable_voice_notes is true")
    if config.runtime.max_reply_chunk_chars <= 0:
        raise ValueError("runtime.max_reply_chunk_chars must be > 0")

    _validate_telegram_collector_token_ownership(config, config_path=config_path)


def load_config(path: str | Path) -> BotConfig:
    config_path = Path(path)
    raw = _read_json(config_path)

    telegram_raw = raw.get("telegram")
    signal_raw = raw.get("signal")
    telegram = TelegramConfig(**telegram_raw) if isinstance(telegram_raw, dict) else None
    signal = SignalConfig(**signal_raw) if isinstance(signal_raw, dict) else None
    llm = LLMConfig(**raw["llm"])
    runtime = RuntimeConfig(**raw.get("runtime", {}))

    config = BotConfig(telegram=telegram, signal=signal, llm=llm, runtime=runtime)
    _validate(config, config_path=config_path)
    return config
