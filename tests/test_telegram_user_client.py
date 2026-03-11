import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from assistant_framework.telegram_user_client import TelegramUserClient
from messaging_llm_bot.interfaces import IncomingMessage
from messaging_llm_bot.telegram_user_client import TelegramUserClient as BotTelegramUserClient


class _FakeClient:
    def __init__(self, authorized: bool) -> None:
        self.authorized = authorized
        self.connected = False
        self.dialog_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return self.authorized

    async def iter_dialogs(self, limit=50):
        self.dialog_calls += 1
        if False:
            yield None


class _FakeMessage:
    def __init__(self, message_id: int, text: str, sender: object) -> None:
        self.id = message_id
        self.message = text
        self.text = text
        self._sender = sender
        self.date = datetime(2024, 1, 1, tzinfo=timezone.utc)

    async def get_sender(self):
        return self._sender


class _FakeMessageWithSenderFallback(_FakeMessage):
    def __init__(self, message_id: int, text: str, sender: object, sender_id: int) -> None:
        super().__init__(message_id, text, sender=None)
        self.sender = sender
        self.sender_id = sender_id


class _FakeDialogClient(_FakeClient):
    def __init__(self, dialogs: list[object], messages_by_chat: dict[int, list[_FakeMessage]]) -> None:
        super().__init__(authorized=True)
        self._dialogs = dialogs
        self._messages_by_chat = messages_by_chat
        self.iter_messages_calls: list[tuple[int, int, int, bool]] = []

    async def iter_dialogs(self, limit=50):
        self.dialog_calls += 1
        for dialog in self._dialogs[:limit]:
            yield dialog

    async def iter_messages(self, entity, limit=20, min_id=0, reverse=False):
        chat_id = int(getattr(entity, "id"))
        self.iter_messages_calls.append((chat_id, limit, min_id, reverse))
        for message in self._messages_by_chat.get(chat_id, []):
            if message.id > min_id:
                yield message


class TelegramUserClientTests(unittest.TestCase):
    def test_get_recent_messages_returns_empty_without_session(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client = TelegramUserClient(api_id=1, api_hash="h", session_path=str(Path(td) / "telegram_user"))
            client._ensure_client = lambda: _FakeClient(authorized=True)  # type: ignore[assignment]

            result = client.get_recent_messages(since_timestamp=None, max_messages=10)

            self.assertEqual(result, [])

    def test_get_recent_messages_does_not_trigger_interactive_login(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session_path = Path(td) / "telegram_user"
            session_path.with_suffix(".session").write_text("present", encoding="utf-8")

            fake_client = _FakeClient(authorized=False)
            client = TelegramUserClient(api_id=1, api_hash="h", session_path=str(session_path))
            client._ensure_client = lambda: fake_client  # type: ignore[assignment]

            result = client.get_recent_messages(since_timestamp=None, max_messages=10)

            self.assertEqual(result, [])
            self.assertTrue(fake_client.connected)
            self.assertEqual(fake_client.dialog_calls, 0)

    def test_extract_sender_name_and_contact_support_channel_accounts(self) -> None:
        sender = type("Sender", (), {"id": 99, "title": "Announcements", "username": "announcements"})()

        self.assertEqual(BotTelegramUserClient._extract_sender_name(sender), "Announcements")
        self.assertEqual(
            BotTelegramUserClient._extract_sender_contact(sender, "Announcements"),
            "Announcements (@announcements)",
        )
        self.assertEqual(TelegramUserClient._extract_sender_name(sender), "Announcements")
        self.assertEqual(
            TelegramUserClient._extract_sender_contact(sender, "Announcements"),
            "Announcements (@announcements)",
        )

    def test_get_recent_messages_includes_sender_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session_path = Path(td) / "telegram_user"
            session_path.with_suffix(".session").write_text("present", encoding="utf-8")
            sender = type("Sender", (), {"id": 7, "first_name": "Alice", "username": "alice"})()
            dialog = type("Dialog", (), {"entity": type("Entity", (), {"id": 42})()})()
            fake_client = _FakeDialogClient(dialogs=[dialog], messages_by_chat={42: [_FakeMessage(5, "hello", sender)]})
            client = TelegramUserClient(api_id=1, api_hash="h", session_path=str(session_path))
            client._ensure_client = lambda: fake_client  # type: ignore[assignment]

            result = client.get_recent_messages(since_timestamp=None, max_messages=10)

            self.assertEqual(
                result,
                [
                    {
                        "chat_id": 42,
                        "message_id": 5,
                        "date": 1704067200,
                        "text": "hello",
                        "sender_id": "7",
                        "sender_name": "Alice",
                        "sender_contact": "Alice (@alice)",
                    }
                ],
            )

    def test_get_recent_messages_uses_message_sender_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session_path = Path(td) / "telegram_user"
            session_path.with_suffix(".session").write_text("present", encoding="utf-8")
            sender = type("Sender", (), {"id": 7, "first_name": "Alice", "username": "alice"})()
            dialog = type("Dialog", (), {"entity": type("Entity", (), {"id": 42})()})()
            fake_client = _FakeDialogClient(
                dialogs=[dialog],
                messages_by_chat={42: [_FakeMessageWithSenderFallback(5, "hello", sender, 7)]},
            )
            client = TelegramUserClient(api_id=1, api_hash="h", session_path=str(session_path))
            client._ensure_client = lambda: fake_client  # type: ignore[assignment]

            result = client.get_recent_messages(since_timestamp=None, max_messages=10)

            self.assertEqual(result[0]["sender_id"], "7")
            self.assertEqual(result[0]["sender_name"], "Alice")
            self.assertEqual(result[0]["sender_contact"], "Alice (@alice)")

    def test_bot_client_persists_last_seen_message_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session_path = Path(td) / "telegram_user"
            sender = type("Sender", (), {"id": 7, "first_name": "Alice", "username": "alice"})()
            dialog = type("Dialog", (), {"entity": type("Entity", (), {"id": 42})()})()
            fake_client = _FakeDialogClient(
                dialogs=[dialog],
                messages_by_chat={42: [_FakeMessage(5, "hello", sender), _FakeMessage(8, "again", sender)]},
            )
            client = BotTelegramUserClient(api_id=1, api_hash="h", session_path=str(session_path))
            client._ensure_client = lambda: fake_client  # type: ignore[assignment]

            updates = client.get_updates()

            self.assertEqual(
                updates,
                [
                    IncomingMessage(
                        update_id=(42 * 1_000_000_000) + 5,
                        backend="telegram",
                        conversation_id="42",
                        sender_id="7",
                        chat_id=42,
                        text="hello",
                        sender_name="Alice",
                        sender_contact="Alice (@alice)",
                        sent_at="2024-01-01T00:00:00+00:00",
                    ),
                    IncomingMessage(
                        update_id=(42 * 1_000_000_000) + 8,
                        backend="telegram",
                        conversation_id="42",
                        sender_id="7",
                        chat_id=42,
                        text="again",
                        sender_name="Alice",
                        sender_contact="Alice (@alice)",
                        sent_at="2024-01-01T00:00:00+00:00",
                    ),
                ],
            )
            self.assertEqual(
                session_path.with_suffix(".updates.json").read_text(encoding="utf-8"),
                '{"42":8}',
            )

    def test_bot_client_uses_persisted_last_seen_message_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session_path = Path(td) / "telegram_user"
            session_path.with_suffix(".updates.json").write_text('{"42":8}', encoding="utf-8")
            sender = type("Sender", (), {"id": 7, "first_name": "Alice"})()
            dialog = type("Dialog", (), {"entity": type("Entity", (), {"id": 42})()})()
            fake_client = _FakeDialogClient(
                dialogs=[dialog],
                messages_by_chat={42: [_FakeMessage(8, "old", sender), _FakeMessage(9, "new", sender)]},
            )
            client = BotTelegramUserClient(api_id=1, api_hash="h", session_path=str(session_path))
            client._ensure_client = lambda: fake_client  # type: ignore[assignment]

            updates = client.get_updates()

            self.assertEqual([update.text for update in updates], ["new"])
            self.assertEqual(fake_client.iter_messages_calls, [(42, client.message_limit, 8, True)])
            self.assertEqual(
                session_path.with_suffix(".updates.json").read_text(encoding="utf-8"),
                '{"42":9}',
            )

    def test_bot_client_uses_message_sender_fallback_when_get_sender_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session_path = Path(td) / "telegram_user"
            sender = type("Sender", (), {"id": 7, "first_name": "Alice", "username": "alice"})()
            dialog = type("Dialog", (), {"entity": type("Entity", (), {"id": 42})()})()
            fake_client = _FakeDialogClient(
                dialogs=[dialog],
                messages_by_chat={42: [_FakeMessageWithSenderFallback(5, "hello", sender, 7)]},
            )
            client = BotTelegramUserClient(api_id=1, api_hash="h", session_path=str(session_path))
            client._ensure_client = lambda: fake_client  # type: ignore[assignment]

            updates = client.get_updates()

            self.assertEqual(len(updates), 1)
            self.assertEqual(updates[0].sender_id, "7")
            self.assertEqual(updates[0].sender_name, "Alice")
            self.assertEqual(updates[0].sender_contact, "Alice (@alice)")


if __name__ == "__main__":
    unittest.main()
