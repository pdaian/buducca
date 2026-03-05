from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .http import HttpClient


@dataclass
class IncomingMessage:
    update_id: int
    chat_id: int
    text: str | None = None
    voice_file_id: str | None = None


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
            voice = message.get("voice", {})
            chat = message.get("chat", {})
            voice_file_id = voice.get("file_id") if isinstance(voice, dict) else None
            if (not text and not voice_file_id) or "id" not in chat:
                continue
            messages.append(
                IncomingMessage(
                    update_id=update["update_id"],
                    chat_id=int(chat["id"]),
                    text=text,
                    voice_file_id=voice_file_id,
                )
            )
        return messages

    def get_file_path(self, file_id: str) -> str:
        data = self.http_client.post_json(f"{self.base_url}/getFile", {"file_id": file_id})
        if not data.get("ok"):
            raise RuntimeError(f"Telegram getFile failed: {data}")
        result = data.get("result", {})
        file_path = result.get("file_path")
        if not file_path:
            raise RuntimeError(f"Telegram getFile missing file_path: {data}")
        return str(file_path)

    def download_file(self, file_path: str) -> bytes:
        token = self.base_url.removeprefix("https://api.telegram.org/bot")
        file_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        return self.http_client.get_bytes(file_url)

    def send_message(self, chat_id: int, text: str) -> None:
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        data = self.http_client.post_json(f"{self.base_url}/sendMessage", payload)
        if not data.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {data}")

    def send_typing_action(self, chat_id: int) -> None:
        payload = {
            "chat_id": chat_id,
            "action": "typing",
        }
        data = self.http_client.post_json(f"{self.base_url}/sendChatAction", payload)
        if not data.get("ok"):
            raise RuntimeError(f"Telegram sendChatAction failed: {data}")
