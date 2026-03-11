from __future__ import annotations

from datetime import timezone
from pathlib import Path


class TelegramUserClient:
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
        if not api_id or not api_hash:
            raise ValueError("user_client requires api_id and api_hash")
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_path = session_path
        self.request_timeout_seconds = request_timeout_seconds
        self.qr_wait_seconds = qr_wait_seconds

    def _ensure_client(self):
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError(
                "telethon is required for user_client mode. Install with: pip install telethon"
            ) from exc

        path = Path(self.session_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return TelegramClient(str(path), self.api_id, self.api_hash)

    def _session_exists(self) -> bool:
        path = Path(self.session_path)
        if path.exists():
            return True
        session_path = path.with_suffix(f"{path.suffix}.session") if path.suffix else path.with_suffix(".session")
        return session_path.exists()

    async def _login_if_needed(self, client, allow_interactive_login: bool) -> bool:
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
        client = self._ensure_client()
        async with client:
            is_authorized = await self._login_if_needed(client, allow_interactive_login=False)
            if not is_authorized:
                return []

            cutoff = since_timestamp or 0
            messages: list[dict] = []
            async for dialog in client.iter_dialogs(limit=max_messages):
                async for message in client.iter_messages(dialog.entity, limit=max_messages):
                    if not message.text:
                        continue
                    timestamp = int(message.date.replace(tzinfo=timezone.utc).timestamp())
                    if timestamp <= cutoff:
                        continue
                    sender = await message.get_sender()
                    sender_entity = await self._resolve_sender_entity(client, message, sender, dialog.entity)
                    sender_name = self._extract_sender_name(sender_entity)
                    sender_contact = self._extract_sender_contact(sender_entity, sender_name)
                    sender_id = getattr(sender_entity, "id", getattr(message, "sender_id", getattr(dialog.entity, "id", None)))
                    messages.append(
                        {
                            "chat_id": getattr(dialog.entity, "id", None),
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
        import asyncio

        if not self._session_exists():
            return []

        return asyncio.run(self._collect(since_timestamp=since_timestamp, max_messages=max_messages))

    async def _signup(self) -> None:
        client = self._ensure_client()
        async with client:
            is_authorized = await self._login_if_needed(client, allow_interactive_login=True)
            if not is_authorized:
                raise RuntimeError("User session is not authorized yet")

    def signup(self) -> None:
        import asyncio

        asyncio.run(self._signup())

    @staticmethod
    async def _resolve_sender_entity(client: object, message: object, sender: object, dialog_entity: object) -> object:
        if sender is not None:
            return sender
        for attr_name in ("sender", "chat"):
            candidate = getattr(message, attr_name, None)
            if candidate is not None:
                return candidate
        for candidate in (
            getattr(message, "sender_id", None),
            getattr(message, "from_id", None),
            getattr(message, "peer_id", None),
            dialog_entity,
        ):
            if candidate is None:
                continue
            get_entity = getattr(client, "get_entity", None)
            if callable(get_entity):
                try:
                    resolved = await get_entity(candidate)
                except Exception:
                    resolved = None
                if resolved is not None:
                    return resolved
            if candidate is not dialog_entity:
                return candidate
        return dialog_entity

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
