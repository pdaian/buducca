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
                    messages.append(
                        {
                            "chat_id": getattr(dialog.entity, "id", None),
                            "message_id": message.id,
                            "date": timestamp,
                            "text": message.text,
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
