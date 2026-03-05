import tempfile
import unittest
from pathlib import Path

from assistant_framework.telegram_user_client import TelegramUserClient


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


if __name__ == "__main__":
    unittest.main()
