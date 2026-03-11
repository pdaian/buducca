from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from .http import HttpClient
from .interfaces import IncomingAttachment, IncomingMessage


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
            text = message.get("text") or message.get("caption")
            voice = message.get("voice", {})
            chat = message.get("chat", {})
            sender = message.get("from")
            sender_chat = message.get("sender_chat")
            effective_sender = sender if isinstance(sender, dict) and sender else sender_chat
            voice_file_id = voice.get("file_id") if isinstance(voice, dict) else None
            attachments = self._extract_attachments(message)
            sent_at = self._extract_sent_at(message.get("date"))
            if (not text and not voice_file_id and not attachments) or "id" not in chat:
                continue
            sender_name = self._extract_sender_name(effective_sender)
            sender_contact = self._extract_sender_contact(effective_sender, sender_name)
            sender_id = effective_sender.get("id") if isinstance(effective_sender, dict) else None
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
                    sent_at=sent_at,
                    attachments=attachments,
                )
            )
        return messages

    @staticmethod
    def _extract_sent_at(raw_timestamp: Any) -> str | None:
        try:
            timestamp = int(raw_timestamp)
        except (TypeError, ValueError):
            return None
        from datetime import datetime, timezone

        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

    def _extract_attachments(self, message: dict[str, Any]) -> list[IncomingAttachment]:
        attachments: list[IncomingAttachment] = []
        document = message.get("document")
        if isinstance(document, dict) and document.get("file_id"):
            attachments.append(
                IncomingAttachment(
                    file_id=str(document["file_id"]),
                    filename=str(document.get("file_name") or "document"),
                    mime_type=str(document.get("mime_type") or "") or None,
                )
            )

        for key in ("audio", "video", "animation"):
            payload = message.get(key)
            if isinstance(payload, dict) and payload.get("file_id"):
                attachments.append(
                    IncomingAttachment(
                        file_id=str(payload["file_id"]),
                        filename=str(payload.get("file_name") or key),
                        mime_type=str(payload.get("mime_type") or "") or None,
                    )
                )

        photos = message.get("photo")
        if isinstance(photos, list) and photos:
            candidates = [item for item in photos if isinstance(item, dict) and item.get("file_id")]
            if candidates:
                best = max(candidates, key=lambda item: int(item.get("file_size") or 0))
                photo_id = str(best["file_id"])
                suffix = mimetypes.guess_extension("image/jpeg") or ".jpg"
                attachments.append(
                    IncomingAttachment(
                        file_id=photo_id,
                        filename=f"photo_{photo_id}{suffix}",
                        mime_type="image/jpeg",
                    )
                )
        return attachments

    @staticmethod
    def _extract_sender_name(sender: Any) -> str | None:
        if not isinstance(sender, dict):
            return None
        title = str(sender.get("title") or "").strip()
        if title:
            return title
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
        title = str(sender.get("title") or "").strip()
        if title:
            if sender_id is not None:
                return f"{title} <id:{sender_id}>"
            return title
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

    def send_file(self, chat_id: int, file_path: str, caption: str | None = None) -> None:
        payload = Path(file_path)
        data = self.http_client.post_multipart(
            f"{self.base_url}/sendDocument",
            fields={"chat_id": chat_id, "caption": caption or ""},
            files={
                "document": (
                    payload.name,
                    payload.read_bytes(),
                    mimetypes.guess_type(payload.name)[0] or "application/octet-stream",
                )
            },
        )
        if not data.get("ok"):
            raise RuntimeError(f"Telegram sendDocument failed: {data}")
