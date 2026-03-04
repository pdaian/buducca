import json
import tempfile
import unittest

from assistant_framework.workspace import Workspace
from collectors.telegram_recent_collector import create_collector


class _FakeBotMessage:
    def __init__(self, update_id: int, chat_id: int, date: int, text: str) -> None:
        self.update_id = update_id
        self.chat_id = chat_id
        self.date = date
        self.text = text


class _FakeBotClient:
    def get_updates(self, offset=None, timeout_seconds=20):
        self.offset = offset
        return [
            _FakeBotMessage(3, 100, 111, "hello"),
            _FakeBotMessage(4, 100, 112, "world"),
        ]


class _FakeUserClient:
    def get_recent_messages(self, since_timestamp=None, max_messages=50):
        self.since = since_timestamp
        return [
            {"chat_id": 42, "message_id": 9, "date": 1700000001, "text": "direct hi"},
        ]


class TelegramRecentCollectorTests(unittest.TestCase):
    def test_collects_bot_and_user_messages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            bot = _FakeBotClient()
            user = _FakeUserClient()
            collector = create_collector({"_bot_client": bot, "_user_client": user, "max_messages": 10})

            collector["run"](workspace)

            output = workspace.read_text("telegram.recent")
            lines = [json.loads(line) for line in output.splitlines()]
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0]["source"], "bot")
            self.assertEqual(lines[-1]["source"], "user")

            state = json.loads(workspace.read_text("collectors/telegram_recent.offset"))
            self.assertEqual(state["bot_offset"], 5)
            self.assertEqual(state["user_last_ts"], 1700000001)

    def test_old_state_integer_is_still_supported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("collectors/telegram_recent.offset", "99")

            bot = _FakeBotClient()
            collector = create_collector({"_bot_client": bot})
            collector["run"](workspace)

            self.assertEqual(bot.offset, 99)


if __name__ == "__main__":
    unittest.main()
