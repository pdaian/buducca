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
    read_only: bool = False


@dataclass
class SignalConfig:
    account: str
    poll_interval_seconds: float = 1.0
    allowed_sender_ids: list[str] = field(default_factory=list)
    allowed_group_ids_when_sender_not_allowed: list[str] = field(default_factory=list)
    receive_command: list[str] = field(default_factory=list)
    send_command: list[str] = field(default_factory=list)
    read_only: bool = False


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
    collector_config_path: str = "agent_config.json"
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

    _ = config_path


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
