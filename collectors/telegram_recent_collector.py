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


def _parse_state(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    if not stripped:
        return {"bot_offset": None, "user_last_ts": None}

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return {
                "bot_offset": parsed.get("bot_offset"),
                "user_last_ts": parsed.get("user_last_ts"),
            }
    except json.JSONDecodeError:
        pass

    try:
        return {"bot_offset": int(stripped), "user_last_ts": None}
    except ValueError:
        return {"bot_offset": None, "user_last_ts": None}


def create_collector(config: dict):
    timeout_seconds = float(config.get("timeout_seconds", 30))
    max_messages = int(config.get("max_messages", 50))

    bot_token = (
        config.get("collector_bot_token")
        or config.get("bot_token")
        or os.environ.get("TELEGRAM_COLLECTOR_BOT_TOKEN", "")
        or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    )
    bot_client = config.get("_bot_client")
    if not bot_client and bot_token:
        bot_client = TelegramLiteClient(bot_token=bot_token, timeout_seconds=timeout_seconds)

    user_client = config.get("_user_client")
    user_config = config.get("user_client") or {}
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
        raise ValueError(
            "telegram_recent_collector requires bot_token/TELEGRAM_BOT_TOKEN or enabled user_client config"
        )

    def _run(workspace: Workspace) -> None:
        state = _parse_state(workspace.read_text(STATE_FILE, default=""))
        now_iso = datetime.now(timezone.utc).isoformat()

        lines = []
        if bot_client:
            updates = bot_client.get_updates(offset=state.get("bot_offset"), timeout_seconds=20)
            limited = updates[-max_messages:]
            for message in limited:
                lines.append(
                    json.dumps(
                        {
                            "source": "bot",
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
                state["bot_offset"] = updates[-1].update_id + 1

        if user_client:
            recent_messages = user_client.get_recent_messages(
                since_timestamp=state.get("user_last_ts"), max_messages=max_messages
            )
            for message in recent_messages:
                lines.append(json.dumps({"source": "user", "received_at": now_iso, **message}, ensure_ascii=False))
            if recent_messages:
                state["user_last_ts"] = max(int(message["date"]) for message in recent_messages)

        if lines:
            workspace.write_text(OUTPUT_FILE, "\n".join(lines) + "\n")
        workspace.write_text(STATE_FILE, json.dumps(state))

    return {"name": NAME, "interval_seconds": INTERVAL_SECONDS, "run": _run}
