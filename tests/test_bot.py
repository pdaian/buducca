import unittest

from telegram_llm_bot.bot import BotRunner
from telegram_llm_bot.config import BotConfig, LLMConfig, RuntimeConfig, TelegramConfig


class DummyTelegram:
    def __init__(self) -> None:
        self.sent = []

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class DummyLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def generate_reply(self, _messages):
        return self.reply


class BotTests(unittest.TestCase):
    def make_bot(self) -> BotRunner:
        cfg = BotConfig(
            telegram=TelegramConfig(bot_token="t"),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        return BotRunner(cfg)

    def test_split_long_message(self) -> None:
        bot = self.make_bot()
        parts = bot._split_for_telegram("a" * 5000)
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0]), 4096)
        self.assertEqual(len(parts[1]), 904)

    def test_handle_message_updates_history_and_sends(self) -> None:
        bot = self.make_bot()
        bot.telegram = DummyTelegram()
        bot.llm = DummyLLM("hello")

        bot._handle_message(1, "hi")

        self.assertEqual(bot.telegram.sent, [(1, "hello")])
        self.assertEqual(len(bot._history[1]), 2)


if __name__ == "__main__":
    unittest.main()
