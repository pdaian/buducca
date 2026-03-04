from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from assistant_framework.telegram_lite import TelegramLiteClient
from assistant_framework.workspace import Workspace

NAME = "telegram_recent"
INTERVAL_SECONDS = 60
STATE_FILE = "collectors/telegram_recent.offset"
OUTPUT_FILE = "telegram.recent"


def create_collector(config: dict):
    bot_token = config.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    timeout_seconds = float(config.get("timeout_seconds", 30))
    max_messages = int(config.get("max_messages", 50))

    if not bot_token:
        raise ValueError("telegram_recent_collector requires bot_token config or TELEGRAM_BOT_TOKEN")

    client = TelegramLiteClient(bot_token=bot_token, timeout_seconds=timeout_seconds)

    def _run(workspace: Workspace) -> None:
        offset_raw = workspace.read_text(STATE_FILE, default="").strip()
        offset = int(offset_raw) if offset_raw else None

        updates = client.get_updates(offset=offset, timeout_seconds=20)
        if not updates:
            return

        limited = updates[-max_messages:]
        lines = []
        for message in limited:
            lines.append(
                json.dumps(
                    {
                        "update_id": message.update_id,
                        "chat_id": message.chat_id,
                        "date": message.date,
                        "received_at": datetime.now(timezone.utc).isoformat(),
                        "text": message.text,
                    },
                    ensure_ascii=False,
                )
            )

        workspace.write_text(OUTPUT_FILE, "\n".join(lines) + "\n")
        workspace.write_text(STATE_FILE, str(updates[-1].update_id + 1))

    return {"name": NAME, "interval_seconds": INTERVAL_SECONDS, "run": _run}
