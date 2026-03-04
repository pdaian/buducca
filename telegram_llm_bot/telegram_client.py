from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .http import HttpClient


@dataclass
class IncomingMessage:
    update_id: int
    chat_id: int
    text: str


class TelegramClient:
    def __init__(self, bot_token: str, http_client: HttpClient) -> None:
        self.http_client = http_client
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def get_updates(self, offset: int | None = None, timeout_seconds: int = 30) -> list[IncomingMessage]:
        payload: dict[str, Any] = {"timeout": timeout_seconds}
        if offset is not None:
            payload["offset"] = offset

        data = self.http_client.post_json(f"{self.base_url}/getUpdates", payload)
        if not data.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {data}")

        messages: list[IncomingMessage] = []
        for update in data.get("result", []):
            message = update.get("message", {})
            text = message.get("text")
            chat = message.get("chat", {})
            if not text or "id" not in chat:
                continue
            messages.append(
                IncomingMessage(
                    update_id=update["update_id"],
                    chat_id=int(chat["id"]),
                    text=text,
                )
            )
        return messages

    def send_message(self, chat_id: int, text: str) -> None:
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        data = self.http_client.post_json(f"{self.base_url}/sendMessage", payload)
        if not data.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {data}")
