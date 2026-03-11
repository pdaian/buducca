from __future__ import annotations

from datetime import timezone
from pathlib import Path

from .telegram_user_client_base import BaseTelegramUserClient


class TelegramUserClient(BaseTelegramUserClient):
    """Telegram user client that authenticates with QR login when no session exists."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_path: str,
        phone: str | None = None,
        request_timeout_seconds: float = 30.0,
        qr_wait_seconds: int = 120,
    ) -> None:
        super().__init__(api_id, api_hash, session_path, error_prefix="user_client")
        self.phone = phone
        self.request_timeout_seconds = request_timeout_seconds
        self.qr_wait_seconds = qr_wait_seconds

    async def _get_connected_client(self):
        return await self._connect_client()

    def _session_exists(self) -> bool:
        path = Path(self.session_path)
        if path.exists():
            return True
        session_path = path.with_suffix(f"{path.suffix}.session") if path.suffix else path.with_suffix(".session")
        return session_path.exists()

    async def _login_if_needed(self, client, allow_interactive_login: bool) -> bool:
        is_connected = getattr(client, "is_connected", None)
        connected = bool(is_connected()) if callable(is_connected) else False
        if not connected:
            await client.connect()
        if await client.is_user_authorized():
            return True

        if not allow_interactive_login:
            return False

        if self.phone:
            await client.send_code_request(self.phone)
            raise RuntimeError(
                "User session not authorized. Complete login once with your phone code using Telethon, "
                "or remove phone to use QR login flow."
            )

        qr_login = await client.qr_login()
        print("[telegram_recent_collector] Scan this QR with Telegram app:")
        print(qr_login.url)
        await qr_login.wait(timeout=self.qr_wait_seconds)
        return await client.is_user_authorized()

    async def _collect(self, since_timestamp: int | None, max_messages: int) -> list[dict]:
        client = await self._get_connected_client()
        is_authorized = await self._login_if_needed(client, allow_interactive_login=False)
        if not is_authorized:
            return []

        cutoff = since_timestamp or 0
        messages: list[dict] = []
        async for dialog in client.iter_dialogs(limit=max_messages):
            entity = self._cache_entity(dialog.entity)
            async for message in client.iter_messages(entity, limit=max_messages):
                if not message.text:
                    continue
                timestamp = int(message.date.replace(tzinfo=timezone.utc).timestamp())
                if timestamp <= cutoff:
                    continue
                sender = getattr(message, "sender", None)
                if sender is None:
                    sender = await message.get_sender()
                sender_entity = await self._resolve_sender_entity(client, message, sender, entity)
                sender_name = self._extract_sender_name(sender_entity)
                sender_contact = self._extract_sender_contact(sender_entity, sender_name)
                sender_id = getattr(sender_entity, "id", getattr(message, "sender_id", getattr(entity, "id", None)))
                messages.append(
                    {
                        "chat_id": getattr(entity, "id", None),
                        "message_id": message.id,
                        "date": timestamp,
                        "text": message.text,
                        "sender_id": str(sender_id) if sender_id is not None else None,
                        "sender_name": sender_name,
                        "sender_contact": sender_contact,
                    }
                )
                if len(messages) >= max_messages:
                    break
            if len(messages) >= max_messages:
                break

        messages.sort(key=lambda item: item["date"])
        return messages

    def get_recent_messages(self, since_timestamp: int | None, max_messages: int) -> list[dict]:
        if not self._session_exists():
            return []

        return self._run(self._collect(since_timestamp=since_timestamp, max_messages=max_messages))

    async def _signup(self) -> None:
        client = await self._get_connected_client()
        is_authorized = await self._login_if_needed(client, allow_interactive_login=True)
        if not is_authorized:
            raise RuntimeError("User session is not authorized yet")

    def signup(self) -> None:
        self._run(self._signup())
