import json
import tempfile
import unittest
from pathlib import Path

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
        self.calls = 0
        self.messages = None

    def generate_reply(self, messages):
        self.calls += 1
        self.messages = messages
        return self.reply


class BotTests(unittest.TestCase):
    def make_bot(self, runtime: RuntimeConfig | None = None) -> BotRunner:
        cfg = BotConfig(
            telegram=TelegramConfig(bot_token="t"),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=runtime or RuntimeConfig(),
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

    def test_handle_message_strips_think_blocks_from_reply_and_history(self) -> None:
        bot = self.make_bot()
        bot.telegram = DummyTelegram()
        bot.llm = DummyLLM("<think>private reasoning</think>hello")

        bot._handle_message(1, "hi")

        self.assertEqual(bot.telegram.sent, [(1, "hello")])
        self.assertEqual(bot._history[1][1]["content"], "hello")


    def test_logs_debug_when_think_blocks_filtered(self) -> None:
        bot = self.make_bot()
        bot.telegram = DummyTelegram()
        bot.llm = DummyLLM("<think>private reasoning</think>hello")

        with self.assertLogs(level="DEBUG") as logs:
            bot._handle_message(1, "hi")

        self.assertTrue(any("Filtered <think> block(s) from llm output" in line for line in logs.output))

    def test_status_command_uses_collector_status_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "workspace"
            ws.mkdir(parents=True)
            status = {
                "collector_count": 1,
                "loop_count": 3,
                "updated_at": "2024-01-01T00:00:00+00:00",
                "collectors": {
                    "telegram_recent": {
                        "runs": 2,
                        "failures": 0,
                        "last_success_at": "2024-01-01T00:00:00+00:00",
                        "last_error_at": None,
                    }
                },
            }
            (ws / "collector_status.json").write_text(json.dumps(status), encoding="utf-8")

            runtime = RuntimeConfig(workspace_dir=str(ws), collector_status_file="collector_status.json")
            bot = self.make_bot(runtime=runtime)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("should-not-be-used")

            bot._handle_message(1, "/status")

            self.assertEqual(bot.llm.calls, 0)
            sent = bot.telegram.sent[0][1]
            self.assertIn("collector:telegram_recent", sent)
            self.assertIn("last_success_at", sent)

    def test_system_prompt_includes_skills(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "echo.py").write_text(
                'NAME = "echo"\nDESCRIPTION = "Echoes user text."\n\n'
                'def run(workspace, args):\n    return args.get("text", "")\n',
                encoding="utf-8",
            )

            runtime = RuntimeConfig(workspace_dir=td, skills_dir=str(skills_dir))
            bot = self.make_bot(runtime=runtime)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("hello")

            bot._handle_message(1, "hi")

            system_prompt = bot.llm.messages[0]["content"]
            self.assertIn("Available skills", system_prompt)
            self.assertIn("echo: Echoes user text.", system_prompt)
            self.assertIn('"skill_call"', system_prompt)

    def test_skill_call_output_executes_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "echo.py").write_text(
                'NAME = "echo"\nDESCRIPTION = "Echoes user text."\n\n'
                'def run(workspace, args):\n    return "echo:" + args.get("text", "")\n',
                encoding="utf-8",
            )

            runtime = RuntimeConfig(workspace_dir=td, skills_dir=str(skills_dir))
            bot = self.make_bot(runtime=runtime)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM('{"skill_call": {"name": "echo", "args": {"text": "hello"}}}')

            bot._handle_message(1, "run the echo skill")

            self.assertEqual(bot.telegram.sent, [(1, "echo:hello")])


if __name__ == "__main__":
    unittest.main()
