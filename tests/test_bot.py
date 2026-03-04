import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telegram_llm_bot.bot import BotRunner
from telegram_llm_bot.http import RequestTimeoutError
from telegram_llm_bot.config import BotConfig, LLMConfig, RuntimeConfig, TelegramConfig
from telegram_llm_bot.telegram_client import IncomingMessage


class DummyTelegram:
    def __init__(self) -> None:
        self.sent = []
        self.file_path = "voice/test.ogg"
        self.file_bytes = b"dummy"

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    def get_file_path(self, file_id: str) -> str:
        return self.file_path

    def download_file(self, file_path: str) -> bytes:
        return self.file_bytes


class DummyLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.messages = None

    def generate_reply(self, messages):
        self.calls += 1
        self.messages = messages
        return self.reply


class BrokenLLM:
    def generate_reply(self, messages):
        raise RuntimeError("llm parse failed")


class TimeoutLLM:
    def generate_reply(self, messages):
        raise RequestTimeoutError("timed out")




class PollingTelegram:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_updates(self, offset=None, timeout_seconds=30):
        self.calls.append((offset, timeout_seconds))
        if not self.responses:
            raise KeyboardInterrupt
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class BotTests(unittest.TestCase):
    def make_bot(self, runtime: RuntimeConfig | None = None) -> BotRunner:
        cfg = BotConfig(
            telegram=TelegramConfig(bot_token="t"),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=runtime or RuntimeConfig(),
        )
        return BotRunner(cfg)

    def test_llm_verbose_debug_enabled_when_log_level_is_debug(self) -> None:
        runtime = RuntimeConfig(log_level="DEBUG", debug=False)
        bot = self.make_bot(runtime=runtime)

        self.assertTrue(bot.llm.debug)

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

    def test_skill_call_parses_json_after_think_block(self) -> None:
        bot = self.make_bot()

        parsed = bot._try_parse_skill_call(
            """I will run that now.
{"skill_call": {"name": "taskwarrior", "args": {"action": "list"}}}"""
        )

        self.assertEqual(parsed, {"name": "taskwarrior", "args": {"action": "list"}})

    def test_skill_call_parses_first_valid_json_block(self) -> None:
        bot = self.make_bot()

        parsed = bot._try_parse_skill_call(
            """Great idea.
{"note": "not a tool call"}
{"skill_call": {"name": "echo", "args": {"text": "hi"}}}"""
        )

        self.assertEqual(parsed, {"name": "echo", "args": {"text": "hi"}})

    def test_skill_call_parse_short_circuits_when_skill_call_not_mentioned(self) -> None:
        bot = self.make_bot()
        decoder_path = "telegram_llm_bot.bot.json.JSONDecoder.raw_decode"

        with patch(decoder_path) as raw_decode:
            parsed = bot._try_parse_skill_call("{" * 2000)

        self.assertIsNone(parsed)
        raw_decode.assert_not_called()

    def test_handle_message_replies_when_llm_generation_fails(self) -> None:
        bot = self.make_bot()
        bot.telegram = DummyTelegram()
        bot.llm = BrokenLLM()

        bot._handle_message(1, "hi")

        self.assertEqual(
            bot.telegram.sent,
            [
                (
                    1,
                    "I ran into an internal error while handling that request. Please try again.",
                )
            ],
        )

    def test_handle_message_replies_when_llm_request_times_out(self) -> None:
        bot = self.make_bot()
        bot.telegram = DummyTelegram()
        bot.llm = TimeoutLLM()

        bot._handle_message(1, "hi")

        self.assertEqual(
            bot.telegram.sent,
            [
                (
                    1,
                    "The language model request timed out after 30s. "
                    "Increase runtime.request_timeout_seconds in config.json if your model needs more time.",
                )
            ],
        )

    def test_transcribe_voice_note_reads_whisper_txt_output(self) -> None:
        bot = self.make_bot(
            runtime=RuntimeConfig(
                enable_voice_notes=True,
                voice_transcribe_command=[
                    "python3",
                    "-c",
                    (
                        "import sys; from pathlib import Path; p = Path(sys.argv[1]); "
                        "Path(sys.argv[2], p.stem + '.txt').write_text('whisper transcript', encoding='utf-8')"
                    ),
                    "{input}",
                    "{input_dir}",
                ],
            )
        )
        bot.telegram = DummyTelegram()

        transcript = bot._transcribe_voice_note("voice-id")

        self.assertEqual(transcript, "whisper transcript")

    def test_handle_voice_update_uses_transcript(self) -> None:
        bot = self.make_bot(runtime=RuntimeConfig(enable_voice_notes=True, voice_transcribe_command=["cat", "{input}"]))
        bot.telegram = DummyTelegram()
        bot.llm = DummyLLM("heard")
        bot._transcribe_voice_note = lambda _fid: "turn on lights"

        bot._handle_update(IncomingMessage(update_id=1, chat_id=1, voice_file_id="voice-id"))

        self.assertEqual(bot.telegram.sent, [(1, "heard")])
        self.assertIn("Voice note transcript", bot.llm.messages[-1]["content"])


    def test_run_forever_skips_pending_updates_on_startup_by_default(self) -> None:
        bot = self.make_bot()
        bot.telegram = PollingTelegram(
            responses=[
                [IncomingMessage(update_id=5, chat_id=1, text="old")],
                KeyboardInterrupt(),
            ]
        )

        bot.run_forever()

        self.assertEqual(bot.telegram.calls[0], (None, 0))
        self.assertEqual(bot.telegram.calls[1], (6, bot.config.telegram.long_poll_timeout_seconds))


    def test_run_forever_treats_poll_timeout_as_retryable(self) -> None:
        bot = self.make_bot()
        bot.telegram = PollingTelegram(responses=[RequestTimeoutError("timed out"), KeyboardInterrupt()])

        with self.assertLogs(level="DEBUG") as logs:
            bot.run_forever()

        self.assertTrue(any("Long-poll request timed out; retrying" in line for line in logs.output))

    def test_run_forever_can_process_pending_updates_when_enabled(self) -> None:
        cfg = BotConfig(
            telegram=TelegramConfig(
                bot_token="t",
                process_pending_updates_on_startup=True,
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.telegram = PollingTelegram(responses=[KeyboardInterrupt()])

        bot.run_forever()

        self.assertEqual(bot.telegram.calls[0], (None, bot.config.telegram.long_poll_timeout_seconds))

    def test_handle_voice_update_replies_on_transcription_error(self) -> None:
        bot = self.make_bot(runtime=RuntimeConfig(enable_voice_notes=True, voice_transcribe_command=["cat", "{input}"]))
        bot.telegram = DummyTelegram()
        bot.llm = DummyLLM("unused")

        def _boom(_fid: str):
            raise RuntimeError("bad")

        bot._transcribe_voice_note = _boom

        bot._handle_update(IncomingMessage(update_id=1, chat_id=1, voice_file_id="voice-id"))

        self.assertEqual(bot.telegram.sent, [(1, "I could not transcribe that voice note locally.")])


if __name__ == "__main__":
    unittest.main()
