from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from assistant_framework.telegram_lite import TelegramLiteClient
from assistant_framework.telegram_user_client import TelegramUserClient
from assistant_framework.workspace import Workspace

NAME = "telegram_recent"
INTERVAL_SECONDS = 60
STATE_FILE = "collectors/telegram_recent.offset"
OUTPUT_FILE = "telegram.recent"
FILE_STRUCTURE = ["collectors/telegram_recent/__init__.py", "collectors/telegram_recent/README.md"]


def _normalize_token(raw: Any) -> str:
    return str(raw or "").strip()


def _parse_state(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    if not stripped:
        return {"accounts": {"default": {"bot_offset": None, "user_last_ts": None}}}

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            if "accounts" in parsed:
                return parsed
            if "bot_offset" in parsed or "user_last_ts" in parsed:
                return {"accounts": {"default": {"bot_offset": parsed.get("bot_offset"), "user_last_ts": parsed.get("user_last_ts")}}}
    except json.JSONDecodeError:
        pass

    try:
        return {"accounts": {"default": {"bot_offset": int(stripped), "user_last_ts": None}}}
    except ValueError:
        return {"accounts": {"default": {"bot_offset": None, "user_last_ts": None}}}


def _build_account(account_cfg: dict, timeout_seconds: float, default_bot_token: str) -> dict[str, Any]:
    bot_token = (
        account_cfg.get("collector_bot_token")
        or account_cfg.get("bot_token")
        or default_bot_token
        or os.environ.get("TELEGRAM_COLLECTOR_BOT_TOKEN", "")
        or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    )
    bot_client = account_cfg.get("_bot_client")
    if not bot_client and bot_token:
        bot_client = TelegramLiteClient(bot_token=bot_token, timeout_seconds=timeout_seconds)

    user_client = account_cfg.get("_user_client")
    user_config = account_cfg.get("user_client") or {}
    use_user_client = bool(user_config.get("enabled", False))
    if use_user_client and not user_client:
        user_client = TelegramUserClient(
            api_id=int(user_config.get("api_id") or os.environ.get("TELEGRAM_API_ID", "0")),
            api_hash=str(user_config.get("api_hash") or os.environ.get("TELEGRAM_API_HASH", "")),
            session_path=str(user_config.get("session_path", "workspace/collectors/telegram_user")),
            phone=str(user_config.get("phone") or os.environ.get("TELEGRAM_PHONE", "")) or None,
            request_timeout_seconds=timeout_seconds,
            qr_wait_seconds=int(user_config.get("qr_wait_seconds", 120)),
        )

    if not bot_client and not user_client:
        raise ValueError("telegram_recent collector requires a bot token or enabled user_client config")

    return {"bot_client": bot_client, "user_client": user_client}


def _resolve_collector_bot_token(account_cfg: dict[str, Any], default_bot_token: str) -> str:
    return (
        _normalize_token(account_cfg.get("collector_bot_token"))
        or _normalize_token(account_cfg.get("bot_token"))
        or _normalize_token(default_bot_token)
    )


def _validate_frontend_token_not_reused(config: dict, accounts_cfg: list[dict[str, Any]]) -> None:
    frontend_token = _normalize_token(config.get("frontend_bot_token"))
    if not frontend_token:
        return

    default_bot_token = _normalize_token(config.get("collector_bot_token") or config.get("bot_token"))
    for account_cfg in accounts_cfg:
        account_name = str(account_cfg.get("name") or "default")
        effective_bot_token = _resolve_collector_bot_token(account_cfg, default_bot_token)
        if effective_bot_token and effective_bot_token == frontend_token:
            raise ValueError(
                "telegram_recent collector account "
                f"'{account_name}' reuses the frontend telegram bot token. "
                "Only one getUpdates consumer may own a bot token. "
                "Use user_client.enabled=true or a separate collector_bot_token."
            )


def create_collector(config: dict):
    timeout_seconds = float(config.get("timeout_seconds", 30))
    max_messages = int(config.get("max_messages", 50))

    default_bot_token = config.get("collector_bot_token") or config.get("bot_token")
    accounts_cfg = config.get("accounts") or [{"name": config.get("account_name", "default"), **config}]
    _validate_frontend_token_not_reused(config, accounts_cfg)
    accounts: dict[str, dict[str, Any]] = {}
    for account_cfg in accounts_cfg:
        account_name = str(account_cfg.get("name") or "default")
        accounts[account_name] = _build_account(account_cfg, timeout_seconds=timeout_seconds, default_bot_token=default_bot_token)

    def _run(workspace: Workspace) -> None:
        state = _parse_state(workspace.read_text(STATE_FILE, default=""))
        account_state = state.setdefault("accounts", {})
        now_iso = datetime.now(timezone.utc).isoformat()

        lines = []
        for account_name, clients in accounts.items():
            current = account_state.setdefault(account_name, {"bot_offset": None, "user_last_ts": None})
            bot_client = clients["bot_client"]
            user_client = clients["user_client"]

            if bot_client:
                updates = bot_client.get_updates(offset=current.get("bot_offset"), timeout_seconds=20)
                limited = updates[-max_messages:]
                for message in limited:
                    lines.append(
                        json.dumps(
                            {
                                "source": "bot",
                                "account": account_name,
                                "update_id": message.update_id,
                                "chat_id": message.chat_id,
                                "date": message.date,
                                "received_at": now_iso,
                                "text": message.text,
                            },
                            ensure_ascii=False,
                        )
                    )
                if updates:
                    current["bot_offset"] = updates[-1].update_id + 1

            if user_client:
                recent_messages = user_client.get_recent_messages(
                    since_timestamp=current.get("user_last_ts"), max_messages=max_messages
                )
                for message in recent_messages:
                    lines.append(
                        json.dumps(
                            {"source": "user", "account": account_name, "received_at": now_iso, **message},
                            ensure_ascii=False,
                        )
                    )
                if recent_messages:
                    current["user_last_ts"] = max(int(message["date"]) for message in recent_messages)

        if lines:
            workspace.write_text(OUTPUT_FILE, "\n".join(lines) + "\n")

        default_state = account_state.get("default", {"bot_offset": None, "user_last_ts": None})
        state["bot_offset"] = default_state.get("bot_offset")
        state["user_last_ts"] = default_state.get("user_last_ts")
        workspace.write_text(STATE_FILE, json.dumps(state))

    return {"name": NAME, "interval_seconds": INTERVAL_SECONDS, "run": _run}
