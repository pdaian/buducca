from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from datetime import timezone
from pathlib import Path

from .interfaces import IncomingAttachment, IncomingMessage


class TelegramUserClient:
    _MAX_FLOOD_WAIT_RETRIES = 3

    def __init__(self, api_id: int, api_hash: str, session_path: str, dialog_limit: int = 50, message_limit: int = 20) -> None:
        if not api_id or not api_hash:
            raise ValueError("telegram user mode requires api_id and api_hash")
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = session_path
        self.dialog_limit = dialog_limit
        self.message_limit = message_limit
        self._last_message_ids = self._load_state()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: object | None = None
        self._entity_cache: dict[int, object] = {}

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

    def _ensure_client(self):
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError("telethon is required for telegram user mode. Install with: pip install telethon") from exc

        path = Path(self.session_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return TelegramClient(str(path), self.api_id, self.api_hash)

    def _run(self, awaitable):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop.run_until_complete(awaitable)

    async def _get_connected_client(self):
        if self._client is None:
            self._client = self._ensure_client()
        client = self._client
        is_connected = getattr(client, "is_connected", None)
        connected = bool(is_connected()) if callable(is_connected) else False
        if not connected:
            await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram user session is not authorized. Run an initial Telethon login for this session file first."
            )
        return client

    @staticmethod
    def _entity_cache_key(entity: object) -> int | None:
        candidate = getattr(entity, "id", entity)
        try:
            return int(candidate)
        except (TypeError, ValueError):
            return None

    def _cache_entity(self, entity: object) -> object:
        cache_key = self._entity_cache_key(entity)
        if cache_key is not None:
            self._entity_cache[cache_key] = entity
        return entity

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
                attachments = await self._extract_attachments(message)
                if not text and not attachments:
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

    async def _resolve_sender_entity(self, client: object, message: object, sender: object, dialog_entity: object) -> object:
        if sender is not None:
            return self._cache_entity(sender)
        for attr_name in ("sender", "chat"):
            candidate = getattr(message, attr_name, None)
            if candidate is not None:
                return self._cache_entity(candidate)
        for candidate in (
            getattr(message, "sender_id", None),
            getattr(message, "from_id", None),
            getattr(message, "peer_id", None),
            dialog_entity,
        ):
            if candidate is None:
                continue
            cache_key = self._entity_cache_key(candidate)
            if cache_key is not None and cache_key in self._entity_cache:
                return self._entity_cache[cache_key]
            get_entity = getattr(client, "get_entity", None)
            if callable(get_entity):
                try:
                    resolved = await get_entity(candidate)
                except Exception:
                    resolved = None
                if resolved is not None:
                    return self._cache_entity(resolved)
            if candidate is not dialog_entity:
                return self._cache_entity(candidate)
        return self._cache_entity(dialog_entity)

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

    def close(self) -> None:
        client = self._client
        if client is not None and self._loop is not None and not self._loop.is_closed():
            disconnect = getattr(client, "disconnect", None)
            if callable(disconnect):
                try:
                    self._loop.run_until_complete(disconnect())
                except Exception:
                    pass
        self._client = None
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._loop = None

    def __del__(self) -> None:
        self.close()

    def send_typing_action(self, chat_id: int) -> None:
        _ = chat_id

    async def _extract_attachments(self, message: object) -> list[IncomingAttachment]:
        attachment = await self._extract_attachment(message)
        return [attachment] if attachment else []

    async def _extract_attachment(self, message: object) -> IncomingAttachment | None:
        if not getattr(message, "media", None):
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

        payload = await message.download_media(file=bytes)
        if not isinstance(payload, (bytes, bytearray)):
            return None
        return IncomingAttachment(
            filename=filename,
            mime_type=mime_type,
            content=bytes(payload),
        )

    @staticmethod
    def _extract_sender_name(sender: object) -> str | None:
        title = str(getattr(sender, "title", "") or "").strip()
        if title:
            return title
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
        title = str(getattr(sender, "title", "") or "").strip()
        if title:
            if sender_id is not None:
                return f"{title} <id:{sender_id}>"
            return title
        if sender_id is not None:
            if sender_name:
                return f"{sender_name} <id:{sender_id}>"
            return f"id:{sender_id}"
        return sender_name
