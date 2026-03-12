from __future__ import annotations

import json
import logging
import mimetypes
from datetime import timezone
from pathlib import Path

from assistant_framework.telegram_user_client_base import BaseTelegramUserClient

from .interfaces import IncomingAttachment, IncomingMessage


class TelegramUserClient(BaseTelegramUserClient):
    _MAX_FLOOD_WAIT_RETRIES = 3
    _FILE_TOKEN_PREFIX = "tguser"

    def __init__(self, api_id: int, api_hash: str, session_path: str, dialog_limit: int = 50, message_limit: int = 20) -> None:
        super().__init__(api_id, api_hash, session_path, error_prefix="telegram user mode")
        self.dialog_limit = dialog_limit
        self.message_limit = message_limit
        self._last_message_ids = self._load_state()

    def _state_path(self) -> Path:
        session = Path(self.session_path)
        if session.suffix:
            return session.with_suffix(f"{session.suffix}.updates.json")
        return session.with_suffix(".updates.json")

    def _load_state(self) -> dict[int, int]:
        state_path = self._state_path()
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        if not isinstance(raw, dict):
            return {}
        state: dict[int, int] = {}
        for chat_id, message_id in raw.items():
            try:
                parsed_chat_id = int(chat_id)
                parsed_message_id = int(message_id)
            except (TypeError, ValueError):
                continue
            if parsed_message_id > 0:
                state[parsed_chat_id] = parsed_message_id
        return state

    def _save_state(self) -> None:
        state_path = self._state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = {str(chat_id): message_id for chat_id, message_id in sorted(self._last_message_ids.items())}
        state_path.write_text(json.dumps(serialized, separators=(",", ":")), encoding="utf-8")

    async def _get_connected_client(self):
        client = await self._connect_client()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram user session is not authorized. Run an initial Telethon login for this session file first."
            )
        return client

    async def _get_updates_async(self) -> list[IncomingMessage]:
        client = await self._get_connected_client()
        updates: list[IncomingMessage] = []
        state_changed = False
        async for dialog in client.iter_dialogs(limit=self.dialog_limit):
            entity = self._cache_entity(dialog.entity)
            chat_id = int(getattr(entity, "id", 0) or 0)
            if not chat_id:
                continue
            conversation_name = self._extract_sender_name(entity)
            min_id = self._last_message_ids.get(chat_id, 0)
            max_id = min_id
            async for message in client.iter_messages(entity, limit=self.message_limit, min_id=min_id, reverse=True):
                max_id = max(max_id, int(getattr(message, "id", 0) or 0))
                text = str(getattr(message, "message", "") or "").strip()
                voice_file_id = self._build_file_token(chat_id, int(getattr(message, "id", 0) or 0)) if self._is_voice_message(message) else None
                attachments = self._extract_attachments(message, chat_id=chat_id)
                if not text and not voice_file_id and not attachments:
                    continue
                sender = getattr(message, "sender", None)
                if sender is None:
                    sender = await message.get_sender()
                sender_entity = await self._resolve_sender_entity(client, message, sender, entity)
                sender_id = int(getattr(sender_entity, "id", getattr(message, "sender_id", chat_id)) or chat_id)
                sender_name = self._extract_sender_name(sender_entity)
                sender_contact = self._extract_sender_contact(sender_entity, sender_name)
                sent_at = getattr(message, "date", None)
                updates.append(
                    IncomingMessage(
                        update_id=(chat_id * 1_000_000_000) + int(message.id),
                        backend="telegram",
                        conversation_id=str(chat_id),
                        conversation_name=conversation_name,
                        sender_id=str(sender_id),
                        chat_id=chat_id,
                        text=text,
                        sender_name=sender_name,
                        sender_contact=sender_contact,
                        sent_at=sent_at.astimezone(timezone.utc).isoformat() if sent_at else None,
                        is_outgoing=bool(getattr(message, "out", False)),
                        voice_file_id=voice_file_id,
                        attachments=attachments,
                    )
                )
            if max_id > min_id:
                self._last_message_ids[chat_id] = max_id
                state_changed = True

        if state_changed:
            self._save_state()

        updates.sort(key=lambda item: item.update_id)
        return updates

    def get_updates(self, offset: int | None = None, timeout_seconds: int = 30) -> list[IncomingMessage]:
        _ = offset
        _ = timeout_seconds
        return self._run(self._get_updates_async())

    async def _run_with_flood_wait_retry(self, operation_name: str, chat_id: int, callback) -> None:
        try:
            from telethon.errors.rpcerrorlist import FloodWaitError
        except ImportError:
            await callback()
            return

        for attempt in range(1, self._MAX_FLOOD_WAIT_RETRIES + 1):
            try:
                await callback()
                return
            except FloodWaitError as exc:
                wait_seconds = max(int(getattr(exc, "seconds", 0) or 0), 1)
                logging.warning(
                    "Telegram %s hit flood wait for chat_id=%s; sleeping %ss before retry %s/%s",
                    operation_name,
                    chat_id,
                    wait_seconds,
                    attempt,
                    self._MAX_FLOOD_WAIT_RETRIES,
                )
                await asyncio.sleep(wait_seconds)
        await callback()

    async def _send_message_async(self, chat_id: int, text: str) -> None:
        client = await self._get_connected_client()
        await self._run_with_flood_wait_retry(
            "send_message",
            chat_id,
            lambda: client.send_message(chat_id, text),
        )

    def send_message(self, chat_id: int, text: str) -> None:
        self._run(self._send_message_async(chat_id, text))

    async def _send_file_async(self, chat_id: int, file_path: str, caption: str | None = None) -> None:
        client = await self._get_connected_client()
        await self._run_with_flood_wait_retry(
            "send_file",
            chat_id,
            lambda: client.send_file(chat_id, file_path, caption=caption or None),
        )

    def send_file(self, chat_id: int, file_path: str, caption: str | None = None) -> None:
        self._run(self._send_file_async(chat_id, file_path, caption))

    def send_typing_action(self, chat_id: int) -> None:
        _ = chat_id

    @classmethod
    def _build_file_token(cls, chat_id: int, message_id: int) -> str:
        return f"{cls._FILE_TOKEN_PREFIX}:{chat_id}:{message_id}"

    @classmethod
    def _parse_file_token(cls, value: str) -> tuple[int, int]:
        prefix, chat_id, message_id = value.split(":", 2)
        if prefix != cls._FILE_TOKEN_PREFIX:
            raise ValueError(f"Unsupported telegram user file token: {value}")
        return int(chat_id), int(message_id)

    @staticmethod
    def _is_voice_message(message: object) -> bool:
        return bool(getattr(message, "voice", None) is not None or getattr(message, "audio", None) is not None)

    def _extract_attachments(self, message: object, *, chat_id: int) -> list[IncomingAttachment]:
        attachment = self._extract_attachment(message, chat_id=chat_id)
        return [attachment] if attachment else []

    def _extract_attachment(self, message: object, *, chat_id: int) -> IncomingAttachment | None:
        if not getattr(message, "media", None):
            return None
        if self._is_voice_message(message):
            return None

        filename = None
        mime_type = None
        document = getattr(message, "document", None)
        if document is not None:
            mime_type = str(getattr(document, "mime_type", "") or "") or None
            for attribute in getattr(document, "attributes", []) or []:
                candidate = str(getattr(attribute, "file_name", "") or "").strip()
                if candidate:
                    filename = candidate
                    break
        elif getattr(message, "photo", None) is not None:
            filename = f"photo_{getattr(message, 'id', 'telegram')}.jpg"
            mime_type = "image/jpeg"

        if not filename:
            suffix = mimetypes.guess_extension(mime_type or "") or ""
            filename = f"attachment_{getattr(message, 'id', 'telegram')}{suffix}"
        return IncomingAttachment(
            file_id=self._build_file_token(chat_id, int(getattr(message, "id", 0) or 0)),
            filename=filename,
            mime_type=mime_type,
        )

    async def _download_file_async(self, file_token: str) -> bytes:
        chat_id, message_id = self._parse_file_token(file_token)
        client = await self._get_connected_client()
        entity = self._entity_cache.get(chat_id)
        if entity is None:
            entity = self._cache_entity(await client.get_entity(chat_id))
        message = await client.get_messages(entity, ids=message_id)
        if message is None or not getattr(message, "media", None):
            raise RuntimeError(f"Telegram user attachment is unavailable for chat_id={chat_id} message_id={message_id}")
        payload = await message.download_media(file=bytes)
        if not isinstance(payload, (bytes, bytearray)):
            raise RuntimeError(f"Telegram user attachment download failed for chat_id={chat_id} message_id={message_id}")
        return bytes(payload)

    def get_file_path(self, file_id: str) -> str:
        return file_id

    def download_file(self, file_path: str) -> bytes:
        return self._run(self._download_file_async(file_path))
