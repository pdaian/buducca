from __future__ import annotations

import asyncio
from pathlib import Path

from .interfaces import IncomingMessage


class TelegramUserClient:
    def __init__(self, api_id: int, api_hash: str, session_path: str, dialog_limit: int = 50, message_limit: int = 20) -> None:
        if not api_id or not api_hash:
            raise ValueError("telegram user mode requires api_id and api_hash")
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = session_path
        self.dialog_limit = dialog_limit
        self.message_limit = message_limit
        self._last_message_ids: dict[int, int] = {}

    def _ensure_client(self):
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError("telethon is required for telegram user mode. Install with: pip install telethon") from exc

        path = Path(self.session_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return TelegramClient(str(path), self.api_id, self.api_hash)

    async def _get_updates_async(self) -> list[IncomingMessage]:
        client = self._ensure_client()
        async with client:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Telegram user session is not authorized. Run an initial Telethon login for this session file first."
                )

            updates: list[IncomingMessage] = []
            async for dialog in client.iter_dialogs(limit=self.dialog_limit):
                entity = dialog.entity
                chat_id = int(getattr(entity, "id", 0) or 0)
                if not chat_id:
                    continue
                min_id = self._last_message_ids.get(chat_id, 0)
                max_id = min_id
                async for message in client.iter_messages(entity, limit=self.message_limit, min_id=min_id, reverse=True):
                    max_id = max(max_id, int(getattr(message, "id", 0) or 0))
                    text = str(getattr(message, "message", "") or "").strip()
                    if not text:
                        continue
                    sender = await message.get_sender()
                    sender_id = int(getattr(sender, "id", chat_id) or chat_id)
                    sender_name = self._extract_sender_name(sender)
                    sender_contact = self._extract_sender_contact(sender, sender_name)
                    updates.append(
                        IncomingMessage(
                            update_id=(chat_id * 1_000_000_000) + int(message.id),
                            backend="telegram",
                            conversation_id=str(chat_id),
                            sender_id=str(sender_id),
                            chat_id=chat_id,
                            text=text,
                            sender_name=sender_name,
                            sender_contact=sender_contact,
                        )
                    )
                if max_id > min_id:
                    self._last_message_ids[chat_id] = max_id

            updates.sort(key=lambda item: item.update_id)
            return updates

    def get_updates(self, offset: int | None = None, timeout_seconds: int = 30) -> list[IncomingMessage]:
        _ = offset
        _ = timeout_seconds
        return asyncio.run(self._get_updates_async())

    async def _send_message_async(self, chat_id: int, text: str) -> None:
        client = self._ensure_client()
        async with client:
            await client.send_message(chat_id, text)

    def send_message(self, chat_id: int, text: str) -> None:
        asyncio.run(self._send_message_async(chat_id, text))

    def send_typing_action(self, chat_id: int) -> None:
        _ = chat_id

    @staticmethod
    def _extract_sender_name(sender: object) -> str | None:
        first_name = str(getattr(sender, "first_name", "") or "").strip()
        last_name = str(getattr(sender, "last_name", "") or "").strip()
        full_name = " ".join(part for part in [first_name, last_name] if part)
        if full_name:
            return full_name
        username = str(getattr(sender, "username", "") or "").strip()
        return username or None

    @staticmethod
    def _extract_sender_contact(sender: object, sender_name: str | None) -> str | None:
        sender_id = getattr(sender, "id", None)
        username = str(getattr(sender, "username", "") or "").strip()
        if username:
            if sender_name and sender_name != username:
                return f"{sender_name} (@{username})"
            return f"@{username}"
        if sender_id is not None:
            if sender_name:
                return f"{sender_name} <id:{sender_id}>"
            return f"id:{sender_id}"
        return sender_name
