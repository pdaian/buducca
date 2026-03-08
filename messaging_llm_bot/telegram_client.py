from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .http import HttpClient


@dataclass
class IncomingMessage:
    update_id: int
    backend: str = "telegram"
    conversation_id: str = ""
    sender_id: str = ""
    chat_id: int | None = None
    text: str | None = None
    voice_file_id: str | None = None
    voice_file_path: str | None = None
    sender_name: str | None = None
    sender_contact: str | None = None

    def __post_init__(self) -> None:
        if self.chat_id is not None:
            if not self.conversation_id:
                self.conversation_id = str(self.chat_id)
            if not self.sender_id:
                self.sender_id = str(self.chat_id)


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
            sender = message.get("from", {})
            voice_file_id = voice.get("file_id") if isinstance(voice, dict) else None
            if (not text and not voice_file_id) or "id" not in chat:
                continue
            sender_name = self._extract_sender_name(sender)
            sender_contact = self._extract_sender_contact(sender, sender_name)
            sender_id = sender.get("id") if isinstance(sender, dict) else None
            messages.append(
                IncomingMessage(
                    update_id=update["update_id"],
                    backend="telegram",
                    conversation_id=str(chat["id"]),
                    sender_id=str(sender_id if sender_id is not None else chat["id"]),
                    chat_id=int(chat["id"]),
                    text=text,
                    voice_file_id=voice_file_id,
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                )
            )
        return messages

    @staticmethod
    def _extract_sender_name(sender: Any) -> str | None:
        if not isinstance(sender, dict):
            return None
        first_name = str(sender.get("first_name") or "").strip()
        last_name = str(sender.get("last_name") or "").strip()
        full_name = " ".join(part for part in [first_name, last_name] if part)
        if full_name:
            return full_name
        username = str(sender.get("username") or "").strip()
        return username or None

    @staticmethod
    def _extract_sender_contact(sender: Any, sender_name: str | None) -> str | None:
        if not isinstance(sender, dict):
            return sender_name

        sender_id = sender.get("id")
        username = str(sender.get("username") or "").strip()
        if username:
            if sender_name and sender_name != username:
                return f"{sender_name} (@{username})"
            return f"@{username}"
        if sender_id is not None:
            if sender_name:
                return f"{sender_name} <id:{sender_id}>"
            return f"id:{sender_id}"
        return sender_name

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
