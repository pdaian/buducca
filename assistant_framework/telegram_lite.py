from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


@dataclass
class LiteTelegramMessage:
    update_id: int
    chat_id: int
    date: int
    text: str


class TelegramLiteClient:
    """Lightweight Telegram client using Bot API token authentication."""

    def __init__(self, bot_token: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.timeout_seconds = timeout_seconds

    def get_updates(self, offset: int | None = None, timeout_seconds: int = 30) -> list[LiteTelegramMessage]:
        payload: dict[str, Any] = {"timeout": timeout_seconds}
        if offset is not None:
            payload["offset"] = offset

        raw = json.dumps(payload).encode("utf-8")
        req = Request(
            url=f"{self.base_url}/getUpdates",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=self.timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {result}")

        messages: list[LiteTelegramMessage] = []
        for update in result.get("result", []):
            message = update.get("message", {})
            text = message.get("text")
            chat = message.get("chat", {})
            if text is None or "id" not in chat:
                continue
            messages.append(
                LiteTelegramMessage(
                    update_id=update["update_id"],
                    chat_id=int(chat["id"]),
                    date=int(message.get("date", 0)),
                    text=text,
                )
            )
        return messages
