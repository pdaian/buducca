from __future__ import annotations

import asyncio
import sys
from pathlib import Path


class BaseTelegramUserClient:
    def __init__(self, api_id: int, api_hash: str, session_path: str, *, error_prefix: str) -> None:
        if not api_id or not api_hash:
            raise ValueError(f"{error_prefix} requires api_id and api_hash")
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = session_path
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: object | None = None
        self._entity_cache: dict[int, object] = {}

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

    async def _connect_client(self):
        if self._client is None:
            self._client = self._ensure_client()
        client = self._client
        is_connected = getattr(client, "is_connected", None)
        connected = bool(is_connected()) if callable(is_connected) else False
        if not connected:
            await client.connect()
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

    def close(self) -> None:
        loop = self._loop
        client = self._client
        self._client = None
        self._loop = None

        if loop is None or loop.is_closed():
            return
        if sys.is_finalizing():
            return

        disconnect = getattr(client, "disconnect", None) if client is not None else None
        if callable(disconnect):
            try:
                loop.run_until_complete(disconnect())
            except Exception:
                pass

        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()

    def __del__(self) -> None:
        if sys.is_finalizing():
            return
        try:
            self.close()
        except Exception:
            pass

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
