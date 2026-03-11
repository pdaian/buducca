import json
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from messaging_llm_bot.bot import BotRunner
from messaging_llm_bot.http import RequestTimeoutError
from messaging_llm_bot.config import BotConfig, GoogleFiConfig, LLMConfig, RuntimeConfig, SignalConfig, TelegramConfig, WhatsAppConfig
from messaging_llm_bot.telegram_client import IncomingMessage
from messaging_llm_bot.interfaces import IncomingAttachment
from messaging_llm_bot.signal_client import SignalFrontendUnavailableError


class DummyTelegram:
    def __init__(self) -> None:
        self.sent = []
        self.typing = []
        self.file_path = "voice/test.ogg"
        self.file_bytes = b"dummy"

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    def send_typing_action(self, chat_id: int) -> None:
        self.typing.append(chat_id)

    def get_file_path(self, file_id: str) -> str:
        return self.file_path

    def download_file(self, file_path: str) -> bytes:
        return self.file_bytes

    def send_file(self, chat_id: int, file_path: str, caption: str | None = None) -> None:
        self.sent.append((chat_id, f"FILE:{file_path}:{caption or ''}"))




class DummyWhatsApp:
    def __init__(self) -> None:
        self.sent = []

    def send_message(self, recipient: str, text: str) -> None:
        self.sent.append((recipient, text))


class DummySignal:
    def __init__(self) -> None:
        self.sent = []

    def send_message(self, recipient: str, text: str) -> None:
        self.sent.append((recipient, text))

class DummyLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.messages = None

    def generate_reply(self, messages):
        self.calls += 1
        self.messages = messages
        return self.reply


class SequentialLLM:
    def __init__(self, replies) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.messages = None

    def generate_reply(self, messages):
        self.calls += 1
        self.messages = messages
        if not self.replies:
            raise RuntimeError("no more replies")
        return self.replies.pop(0)


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


class PollingSignal:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get_updates(self):
        self.calls += 1
        if not self.responses:
            return []
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

    def test_init_does_not_create_workspace_until_runtime_writes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace_dir = Path(td) / "workspace"
            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t"),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=str(workspace_dir)),
            )

            BotRunner(cfg)

            self.assertFalse(workspace_dir.exists())

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
        self.assertEqual(bot.telegram.typing, [1])
        self.assertEqual(len(bot._history[1]), 2)

    def test_read_only_frontend_logs_as_collector_without_reply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t", read_only=True, store_unanswered_messages=True),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("hello")

            bot._handle_update(
                IncomingMessage(update_id=1, backend="telegram", conversation_id="1", sender_id="1", text="collect me")
            )

            self.assertEqual(bot.telegram.sent, [])
            recent = (Path(td) / "telegram.recent").read_text(encoding="utf-8")
            self.assertIn('"text": "collect me"', recent)
            self.assertFalse((Path(td) / "logs" / "agenta_queries.history").exists())

    def test_replied_message_logs_agenta_query(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t"),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("hello")

            bot._handle_update(
                IncomingMessage(update_id=1, backend="telegram", conversation_id="1", sender_id="1", text="hi")
            )

            self.assertEqual(bot.telegram.sent, [(1, "hello")])
            log = (Path(td) / "logs" / "agenta_queries.history").read_text(encoding="utf-8")
            self.assertIn('"query": "hi"', log)
            self.assertIn('"reply": "hello"', log)

    def test_handle_update_saves_attachment_under_dated_workspace_folder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t"),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("received")
            bot.telegram.file_path = "docs/source.pdf"
            bot.telegram.file_bytes = b"%PDF-1.4 dummy"
            bot._save_incoming_attachments = lambda update, **kwargs: "[Attachments]\n- saved: attachments/2026-03-10/telegram_Alice_1710000000.pdf"

            bot._handle_update(
                IncomingMessage(
                    update_id=1,
                    backend="telegram",
                    conversation_id="1",
                    sender_id="1",
                    sender_name="Alice",
                    text="see file",
                    sent_at="2024-03-09T16:00:00+00:00",
                    attachments=[IncomingAttachment(file_id="doc-id", filename="source.pdf", mime_type="application/pdf")],
                )
            )

            self.assertEqual(bot.telegram.sent, [(1, "received")])
            self.assertIn("[Attachments]", bot.llm.messages[-1]["content"])

    def test_hourly_task_sends_to_latest_logged_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t"),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("daily summary")
            bot._append_frontend_log(
                backend="telegram",
                direction="incoming",
                conversation_id="123",
                sender_id="123",
                text="hi",
                logged_at="2026-03-10T13:05:00+00:00",
            )
            Path(td, "hourly").write_text("if it is six o clock send a daily summary", encoding="utf-8")
            bot._current_hourly_slot = lambda: datetime.fromisoformat("2026-03-10T13:00:00-04:00")

            bot._poll_due_hourly_once()

            self.assertEqual(bot.telegram.sent, [(123, "daily summary")])
            self.assertIn("[Hourly routine]", bot.llm.messages[-1]["content"])
            self.assertIn("workspace/hourly", bot.llm.messages[-1]["content"])
            status = json.loads(Path(td, "hourly_status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["last_hourly_slot"], "2026-03-10T13:00:00-04:00")

    def test_hourly_task_no_action_is_not_sent_or_repeated_in_same_hour(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t"),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("NO_ACTION")
            bot._append_frontend_log(
                backend="telegram",
                direction="incoming",
                conversation_id="123",
                sender_id="123",
                text="hi",
                logged_at="2026-03-10T13:05:00+00:00",
            )
            Path(td, "hourly").write_text("only act at six", encoding="utf-8")
            bot._current_hourly_slot = lambda: datetime.fromisoformat("2026-03-10T13:00:00-04:00")

            bot._poll_due_hourly_once()
            bot._poll_due_hourly_once()

            self.assertEqual(bot.telegram.sent, [])
            self.assertEqual(bot.llm.calls, 1)

    def test_action_policy_blocks_mutating_skill_until_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t"),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.telegram = DummyTelegram()
            bot.llm = SequentialLLM(
                [
                    '{"skill_call": {"name": "file", "args": {"action": "write", "paths": ["notes/x.txt"], "content": "hello"}, "done": true}}'
                ]
            )

            bot._handle_message(1, "write a note")

            self.assertEqual(bot.telegram.sent, [(1, "Action requires approval: file.write. Set `assistant/action_policy.json` to allow it, then retry.")])
            audit = Path(td, "audit", "actions.jsonl").read_text(encoding="utf-8")
            self.assertIn('"status": "pending_approval"', audit)

    def test_structured_task_scheduler_sends_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t"),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.telegram = DummyTelegram()
            Path(td, "assistant", "tasks").mkdir(parents=True, exist_ok=True)
            Path(td, "assistant", "tasks", "rent.json").write_text(
                json.dumps(
                    {
                        "id": "rent",
                        "title": "Pay rent",
                        "status": "open",
                        "kind": "task",
                        "due_at": "2026-03-09T12:00:00+00:00",
                        "notify_target": {"backend": "telegram", "conversation_id": "123"},
                    }
                ),
                encoding="utf-8",
            )

            bot._poll_due_structured_schedule_once()
            bot._poll_due_structured_schedule_once()

            self.assertEqual(bot.telegram.sent, [(123, "[Scheduled task]\n- task_id: rent\n- kind: task\n- title: Pay rent")])
            payload = json.loads(Path(td, "assistant", "tasks", "rent.json").read_text(encoding="utf-8"))
            self.assertIn("last_notified_at", payload)

    def test_workspace_evidence_is_cited_and_traced(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t"),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("Your timezone is America/New_York.")
            Path(td, "assistant", "facts").mkdir(parents=True, exist_ok=True)
            Path(td, "assistant", "facts", "timezone.json").write_text(
                json.dumps({"id": "timezone", "statement": "timezone is America/New_York"}),
                encoding="utf-8",
            )

            bot._handle_message(1, "what is my timezone")

            self.assertIn("Sources:\n- assistant/facts/timezone.json", bot.telegram.sent[0][1])
            trace_dir = Path(td, "logs", "traces")
            traces = list(trace_dir.glob("*.json"))
            self.assertEqual(len(traces), 1)
            trace = json.loads(traces[0].read_text(encoding="utf-8"))
            self.assertEqual(trace["final_reply"], bot.telegram.sent[0][1])


    def test_telegram_chat_not_allowed_is_blocked(self) -> None:
        cfg = BotConfig(
            telegram=TelegramConfig(
                bot_token="t",
                allowed_chat_ids=[100],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.telegram = DummyTelegram()
        bot.llm = DummyLLM("hello")

        bot._handle_message("telegram", "200", "22", "hi")

        self.assertEqual(bot.llm.calls, 0)

    def test_telegram_allowed_chat_is_processed(self) -> None:
        cfg = BotConfig(
            telegram=TelegramConfig(
                bot_token="t",
                allowed_chat_ids=[100],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.telegram = DummyTelegram()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_message("telegram", "100", "22", "hi")

        self.assertEqual(bot.llm.calls, 1)

    def test_telegram_user_mode_blocks_chat_not_in_allowed_chat_ids(self) -> None:
        cfg = BotConfig(
            telegram=TelegramConfig(
                mode="user",
                api_id=123,
                api_hash="h",
                allowed_chat_ids=[100],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.telegram = DummyTelegram()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_message("telegram", "200", "22", "hi")

        self.assertEqual(bot.llm.calls, 0)

    def test_telegram_user_mode_allows_chat_in_allowed_chat_ids(self) -> None:
        cfg = BotConfig(
            telegram=TelegramConfig(
                mode="user",
                api_id=123,
                api_hash="h",
                allowed_chat_ids=[100],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.telegram = DummyTelegram()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_message("telegram", "100", "22", "hi")

        self.assertEqual(bot.llm.calls, 1)

    def test_signal_sender_allowed_in_configured_group(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=["+15551112222"],
                allowed_group_ids_when_sender_not_allowed=["AQi7f+/4S3mQv6s5hN2xwQ=="],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_message(
            "signal",
            "group:Family|AQi7f+/4S3mQv6s5hN2xwQ==",
            "+15553334444",
            "hi",
        )

        self.assertEqual(bot.llm.calls, 1)

    def test_signal_sender_not_allowed_outside_configured_group(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=["+15551112222"],
                allowed_group_ids_when_sender_not_allowed=["AQi7f+/4S3mQv6s5hN2xwQ=="],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_message(
            "signal",
            "group:Work|DifferentGroupId==",
            "+15553334444",
            "hi",
        )

        self.assertEqual(bot.llm.calls, 0)

    def test_signal_update_from_unauthorized_sender_is_logged_as_collector_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                signal=SignalConfig(
                    account="+15550001",
                    allowed_sender_ids=["+15551112222"],
                    allowed_group_ids_when_sender_not_allowed=[],
                    store_unanswered_messages=True,
                ),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.signal = object()
            bot.llm = DummyLLM("hello")

            bot._handle_update(
                IncomingMessage(
                    update_id=1,
                    backend="signal",
                    conversation_id="+15550001",
                    sender_id="+15550001",
                    text="self note",
                )
            )

            self.assertEqual(bot.llm.calls, 0)
            signal_history = Path(td) / "logs" / "signal.history"
            self.assertIn("self note", signal_history.read_text(encoding="utf-8"))
            signal_recent = Path(td) / "signal.messages.recent"
            self.assertIn("self note", signal_recent.read_text(encoding="utf-8"))

    def test_unanswered_messages_are_not_stored_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t", read_only=True),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.telegram = DummyTelegram()

            bot._handle_update(
                IncomingMessage(update_id=1, backend="telegram", conversation_id="1", sender_id="1", text="collect me")
            )

            self.assertFalse((Path(td) / "telegram.recent").exists())

    def test_signal_voice_update_from_unauthorized_sender_is_ignored(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=["+15551112222"],
                allowed_group_ids_when_sender_not_allowed=[],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(enable_voice_notes=True, voice_transcribe_command=["cat", "{input}"]),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot.llm = DummyLLM("hello")
        bot._transcribe_voice_file_path = lambda _path: (_ for _ in ()).throw(AssertionError("should not transcribe"))

        bot._handle_update(
            IncomingMessage(
                update_id=1,
                backend="signal",
                conversation_id="+15550001",
                sender_id="+15550001",
                voice_file_path="/tmp/note.ogg",
            )
        )

        self.assertEqual(bot.llm.calls, 0)

    def test_signal_sender_not_allowed_in_direct_message_even_when_group_allowlist_exists(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=[],
                allowed_group_ids_when_sender_not_allowed=["AQi7f+/4S3mQv6s5hN2xwQ=="],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_update(
            IncomingMessage(
                update_id=1,
                backend="signal",
                conversation_id="+15169418131",
                sender_id="+15169418131",
                text="note to self",
            )
        )

        self.assertEqual(bot.llm.calls, 0)

    def test_signal_update_from_unauthorized_sender_allowed_in_configured_group(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=["+15551112222"],
                allowed_group_ids_when_sender_not_allowed=["AQi7f+/4S3mQv6s5hN2xwQ=="],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_update(
            IncomingMessage(
                update_id=1,
                backend="signal",
                conversation_id="group:Family|AQi7f+/4S3mQv6s5hN2xwQ==",
                sender_id="+15553334444",
                text="group note",
            )
        )

        self.assertEqual(bot.llm.calls, 1)

    def test_signal_self_sender_allowed_in_configured_group(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=["+15551112222"],
                allowed_group_ids_when_sender_not_allowed=["AQi7f+/4S3mQv6s5hN2xwQ=="],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_update(
            IncomingMessage(
                update_id=1,
                backend="signal",
                conversation_id="group:Family|AQi7f+/4S3mQv6s5hN2xwQ==",
                sender_id="+15550001",
                text="self note in group",
            )
        )

        self.assertEqual(bot.llm.calls, 1)

    def test_signal_self_sender_with_different_number_format_allowed_via_group_allowlist(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+1 555 0001",
                allowed_sender_ids=["+15551112222"],
                allowed_group_ids_when_sender_not_allowed=["AQi7f+/4S3mQv6s5hN2xwQ=="],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_update(
            IncomingMessage(
                update_id=1,
                backend="signal",
                conversation_id="group:Family|AQi7f+/4S3mQv6s5hN2xwQ==",
                sender_id="+15550001",
                text="self note in group",
            )
        )

        self.assertEqual(bot.llm.calls, 1)

    def test_signal_self_sender_blocked_when_allowed_sender_list_is_empty(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=[],
                allowed_group_ids_when_sender_not_allowed=["AQi7f+/4S3mQv6s5hN2xwQ=="],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_update(
            IncomingMessage(
                update_id=1,
                backend="signal",
                conversation_id="+15550001",
                sender_id="+15550001",
                text="self note",
            )
        )

        self.assertEqual(bot.llm.calls, 0)

    def test_signal_self_sender_blocked_warning_includes_group_id_for_group_messages(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=[],
                allowed_group_ids_when_sender_not_allowed=[],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        with self.assertLogs(level="WARNING") as logs:
            bot._handle_update(
                IncomingMessage(
                    update_id=1,
                    backend="signal",
                    conversation_id="group:Family|DifferentGroupId==",
                    sender_id="+15550001",
                    text="self note",
                )
            )

        self.assertTrue(any("group_id=DifferentGroupId==" in line for line in logs.output))

    def test_signal_self_sender_blocked_warning_omits_group_id_for_direct_messages(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=[],
                allowed_group_ids_when_sender_not_allowed=[],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        with self.assertLogs(level="WARNING") as logs:
            bot._handle_update(
                IncomingMessage(
                    update_id=1,
                    backend="signal",
                    conversation_id="+15550001",
                    sender_id="+15550001",
                    text="self note",
                )
            )

        self.assertTrue(any("signal.allowed_sender_ids" in line for line in logs.output))
        self.assertFalse(any("group_id=" in line for line in logs.output))


    def test_signal_non_group_sender_blocked_when_allowed_sender_list_is_empty(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=[],
                allowed_group_ids_when_sender_not_allowed=["AQi7f+/4S3mQv6s5hN2xwQ=="],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_update(
            IncomingMessage(
                update_id=1,
                backend="signal",
                conversation_id="+15556667777",
                sender_id="+15556667777",
                text="hello",
            )
        )

        self.assertEqual(bot.llm.calls, 0)

    def test_signal_sender_allowlist_supports_number_normalization(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(
                account="+15550001",
                allowed_sender_ids=["+1 555 111 2222"],
                allowed_group_ids_when_sender_not_allowed=[],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_update(
            IncomingMessage(
                update_id=1,
                backend="signal",
                conversation_id="+15551112222",
                sender_id="+15551112222",
                text="hello",
            )
        )

        self.assertEqual(bot.llm.calls, 1)

    def test_whatsapp_read_only_frontend_logs_without_reply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                whatsapp=WhatsAppConfig(
                    account="personal",
                    read_only=True,
                    store_unanswered_messages=True,
                    receive_command=["python3", "recv.py"],
                    send_command=["python3", "send.py", "{recipient}", "{message}"],
                ),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.whatsapp = DummyWhatsApp()
            bot.llm = DummyLLM("hello")

            bot._handle_update(
                IncomingMessage(update_id=1, backend="whatsapp", conversation_id="group:Family|g1", sender_id="+15550001", text="collect me")
            )

            self.assertEqual(bot.whatsapp.sent, [])
            recent = (Path(td) / "whatsapp.messages.recent").read_text(encoding="utf-8")
            self.assertIn('"text": "collect me"', recent)

    def test_whatsapp_sender_allowlist_blocks_non_allowed_sender(self) -> None:
        cfg = BotConfig(
            whatsapp=WhatsAppConfig(
                account="personal",
                allowed_sender_ids=["+15551112222"],
                allowed_group_ids_when_sender_not_allowed=[],
                receive_command=["python3", "recv.py"],
                send_command=["python3", "send.py", "{recipient}", "{message}"],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.whatsapp = DummyWhatsApp()
        bot.llm = DummyLLM("hello")

        bot._handle_message("whatsapp", "group:Family|g1", "+15553334444", "hi")

        self.assertEqual(bot.llm.calls, 0)

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

    def test_handle_message_skips_empty_reply_after_think_filtering(self) -> None:
        cfg = BotConfig(
            signal=SignalConfig(account="+15551230000", allowed_sender_ids=["+15551230000"]),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.signal = DummySignal()
        bot.llm = DummyLLM("<think>private reasoning</think>")

        handled = bot._handle_message("signal", "+15551230000", "+15551230000", "hi")

        self.assertTrue(handled)
        self.assertEqual(bot.signal.sent, [])
        self.assertEqual(bot._history["signal:+15551230000"][1]["content"], "")


    def test_frontend_history_files_created_and_written(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = RuntimeConfig(workspace_dir=td)
            bot = self.make_bot(runtime=runtime)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("hello")

            bot._handle_update(IncomingMessage(update_id=1, chat_id=1, text="hi"))

            telegram_history = Path(td) / "logs" / "telegram.history"
            signal_history = Path(td) / "logs" / "signal.history"
            self.assertTrue(telegram_history.exists())
            self.assertFalse(signal_history.exists())

            events = [json.loads(line) for line in telegram_history.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[0]["direction"], "incoming")
            self.assertEqual(events[0]["text"], "hi")
            self.assertEqual(events[-1]["direction"], "outgoing")
            self.assertEqual(events[-1]["text"], "hello")


    def test_telegram_incoming_log_includes_sender_contact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = RuntimeConfig(workspace_dir=td)
            bot = self.make_bot(runtime=runtime)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("hello")

            update = SimpleNamespace(
                backend="telegram",
                conversation_id="1",
                sender_id="1",
                sender_name="Alice",
                sender_contact="Alice (@alice_tg)",
                text="hi",
                voice_file_id=None,
                voice_file_path=None,
            )
            bot._handle_update(update)

            telegram_history = Path(td) / "logs" / "telegram.history"
            events = [json.loads(line) for line in telegram_history.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[0]["sender_name"], "Alice")
            self.assertEqual(events[0]["sender_contact"], "Alice (@alice_tg)")

    def test_sender_context_is_added_to_llm_prompt(self) -> None:
        bot = self.make_bot()
        bot.telegram = DummyTelegram()
        llm = DummyLLM("hello")
        bot.llm = llm

        bot._handle_message("telegram", "1", "1", "hi", "Alice", "Alice (@alice_tg)")

        prompt = llm.messages
        self.assertEqual(prompt[-1]["role"], "user")
        self.assertIn("[Sender context]", prompt[-1]["content"])
        self.assertIn("telegram_account: Alice (@alice_tg)", prompt[-1]["content"])
        self.assertTrue(prompt[-1]["content"].endswith("\n\nhi"))

    def test_signal_incoming_log_includes_sender_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                signal=SignalConfig(account="+15550001", allowed_sender_ids=["+15550001"]),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.signal = object()
            bot.llm = DummyLLM("hello")
            bot._send_message = lambda backend, conversation_id, text: None

            update = SimpleNamespace(
                backend="signal",
                conversation_id="+15550001",
                sender_id="+15550001",
                sender_name="Alice",
                text="hi",
                voice_file_id=None,
                voice_file_path=None,
            )
            bot._handle_update(update)

            signal_history = Path(td) / "logs" / "signal.history"
            events = [json.loads(line) for line in signal_history.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[0]["sender_name"], "Alice")
            self.assertEqual(events[0]["sender_contact"], "Alice <+15550001>")

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


    def test_poll_frontends_handles_telegram_409_conflict(self) -> None:
        bot = self.make_bot()
        bot.telegram = PollingTelegram([RuntimeError("HTTP 409 for https://api.telegram.org/bott/getUpdates: conflict")])

        with self.assertLogs(level="WARNING") as logs:
            bot._poll_frontends_once()

        self.assertTrue(any("polling conflict" in line.lower() for line in logs.output))

    def test_poll_frontends_reraises_non_conflict_runtime_error(self) -> None:
        bot = self.make_bot()
        bot.telegram = PollingTelegram([RuntimeError("HTTP 500 for https://api.telegram.org/bott/getUpdates: server")])

        with self.assertRaises(RuntimeError):
            bot._poll_frontends_once()


    def test_poll_frontends_uses_backoff_after_telegram_409_conflict(self) -> None:
        bot = self.make_bot()
        bot.telegram = PollingTelegram([RuntimeError("HTTP 409 for https://api.telegram.org/bott/getUpdates: conflict")])

        bot._poll_frontends_once()

        self.assertIsNotNone(bot._telegram_retry_after)
        self.assertEqual(bot._telegram_conflict_backoff_seconds, 10.0)

    def test_poll_frontends_resets_backoff_after_successful_telegram_poll(self) -> None:
        bot = self.make_bot()
        bot._telegram_conflict_backoff_seconds = 20.0
        bot._telegram_retry_after = 0.0
        bot.telegram = PollingTelegram([[], []])

        bot._poll_frontends_once()

        self.assertIsNone(bot._telegram_retry_after)
        self.assertEqual(bot._telegram_conflict_backoff_seconds, 5.0)

    def test_system_prompt_includes_skills(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "echo.py").write_text(
                'NAME = "echo"\nDESCRIPTION = "Echoes user text."\n'
                'ARGS_SCHEMA = "{ text: string }"\n\n'
                'def run(workspace, args):\n    return args.get("text", "")\n',
                encoding="utf-8",
            )

            Path(td, "learnings").write_text(
                "User prefers concise responses.\nRemember to include timezone info.\n",
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
            self.assertIn("args schema", system_prompt)
            self.assertIn("{ text: string }", system_prompt)
            self.assertIn('"skill_call"', system_prompt)
            self.assertIn("Persistent learnings (from workspace/learnings)", system_prompt)
            self.assertIn("These are long-term learnings for future prompts", system_prompt)
            self.assertIn("- User prefers concise responses.", system_prompt)
            self.assertIn("save them with the learn skill as a concise one-line learning", system_prompt)
            self.assertIn("Current date/time (America/New_York, accurate to the minute):", system_prompt)
            self.assertRegex(system_prompt, r"Current date/time \(America/New_York, accurate to the minute\): .* (EST|EDT)")

    def test_system_prompt_includes_configured_file_skill_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "file.py").write_text(
                'NAME = "file"\nDESCRIPTION = "File ops."\n\n'
                'def run(workspace, args):\n    return "ok"\n',
                encoding="utf-8",
            )

            cfg = BotConfig(
                telegram=TelegramConfig(bot_token="t"),
                llm=LLMConfig(
                    base_url="u",
                    api_key="k",
                    model="m",
                    history_messages=2,
                    file_task_layout_prompt="Store everything under assistant/.",
                ),
                runtime=RuntimeConfig(
                    workspace_dir=td,
                    skills_dir=str(skills_dir),
                    file_skill_actions=["read", "append"],
                ),
            )
            bot = BotRunner(cfg)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("hello")

            bot._handle_message(1, "hi")

            system_prompt = bot.llm.messages[0]["content"]
            self.assertIn("For file-based personal assistant tasks, prefer the file skill", system_prompt)
            self.assertIn("Configured file skill actions: read, append.", system_prompt)
            self.assertIn("File organization guidance: Store everything under assistant/.", system_prompt)

    def test_message_send_skill_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "message_send.py").write_text(
                'NAME = "message_send"\nDESCRIPTION = "Sends messages."\n\n'
                'def run(workspace, args):\n    return "ok"\n',
                encoding="utf-8",
            )

            runtime = RuntimeConfig(workspace_dir=td, skills_dir=str(skills_dir))
            bot = self.make_bot(runtime=runtime)

            self.assertNotIn("message_send", bot._skills)

    def test_message_send_skill_can_be_enabled_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "message_send.py").write_text(
                'NAME = "message_send"\nDESCRIPTION = "Sends messages."\n\n'
                'def run(workspace, args):\n    return "ok"\n',
                encoding="utf-8",
            )

            runtime = RuntimeConfig(
                workspace_dir=td,
                skills_dir=str(skills_dir),
                enable_message_send_skill=True,
            )
            bot = self.make_bot(runtime=runtime)

            self.assertIn("message_send", bot._skills)

    def test_system_prompt_includes_loaded_collector_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            collectors_dir = Path(td) / "collectors"
            collectors_dir.mkdir(parents=True)
            (collectors_dir / "demo.py").write_text(
                'NAME = "demo"\n'
                'DESCRIPTION = "Writes demo files."\n'
                'GENERATED_FILES = ["demo.recent", "demo.state.json"]\n'
                'FILE_STRUCTURE = ["collectors/demo.py", "collectors/demo/README.md"]\n'
                "def create_collector(config):\n"
                "    def run(workspace):\n"
                "        return None\n"
                "    return {\n"
                '        "name": NAME,\n'
                '        "description": DESCRIPTION,\n'
                '        "generated_files": GENERATED_FILES,\n'
                '        "file_structure": FILE_STRUCTURE,\n'
                '        "run": run,\n'
                "    }\n",
                encoding="utf-8",
            )
            demo_docs = collectors_dir / "demo"
            demo_docs.mkdir()
            (demo_docs / "README.md").write_text("collector docs\n", encoding="utf-8")
            Path(td, "demo.recent").write_text("recent data\n", encoding="utf-8")
            Path(td, "demo.state.json").write_text("{\"ok\": true}\n", encoding="utf-8")
            Path(td, "agent_config.json").write_text('{"collectors": {"demo": {"enabled": true}}}\n', encoding="utf-8")

            runtime = RuntimeConfig(
                workspace_dir=td,
                skills_dir=str(Path(td) / "skills"),
                collectors_dir=str(collectors_dir),
                collector_config_path=str(Path(td) / "agent_config.json"),
            )
            bot = self.make_bot(runtime=runtime)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("hello")

            bot._handle_message(1, "hi")

            system_prompt = bot.llm.messages[0]["content"]
            self.assertIn("Loaded collector outputs available in the workspace", system_prompt)
            self.assertIn("demo: Writes demo files.", system_prompt)
            self.assertIn("demo.recent", system_prompt)
            self.assertIn("collectors/demo.py", system_prompt)

    def test_system_prompt_omits_missing_or_empty_collector_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            collectors_dir = Path(td) / "collectors"
            collectors_dir.mkdir(parents=True)
            (collectors_dir / "demo.py").write_text(
                'NAME = "demo"\n'
                'DESCRIPTION = "Writes demo files."\n'
                'GENERATED_FILES = ["demo.recent", "demo.empty", "demo.missing"]\n'
                'FILE_STRUCTURE = ["collectors/demo.py", "collectors/demo/README.md", "collectors/demo/missing.md"]\n'
                "def create_collector(config):\n"
                "    def run(workspace):\n"
                "        return None\n"
                "    return {\n"
                '        "name": NAME,\n'
                '        "description": DESCRIPTION,\n'
                '        "generated_files": GENERATED_FILES,\n'
                '        "file_structure": FILE_STRUCTURE,\n'
                '        "run": run,\n'
                "    }\n",
                encoding="utf-8",
            )
            demo_docs = collectors_dir / "demo"
            demo_docs.mkdir()
            (demo_docs / "README.md").write_text("", encoding="utf-8")
            Path(td, "demo.recent").write_text("recent data\n", encoding="utf-8")
            Path(td, "demo.empty").write_text("", encoding="utf-8")
            Path(td, "agent_config.json").write_text('{"collectors": {"demo": {"enabled": true}}}\n', encoding="utf-8")

            runtime = RuntimeConfig(
                workspace_dir=td,
                skills_dir=str(Path(td) / "skills"),
                collectors_dir=str(collectors_dir),
                collector_config_path=str(Path(td) / "agent_config.json"),
            )
            bot = self.make_bot(runtime=runtime)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("hello")

            bot._handle_message(1, "hi")

            system_prompt = bot.llm.messages[0]["content"]
            self.assertIn("demo: Writes demo files.", system_prompt)
            self.assertIn("demo.recent", system_prompt)
            self.assertIn("collectors/demo.py", system_prompt)
            self.assertNotIn("demo.empty", system_prompt)
            self.assertNotIn("demo.missing", system_prompt)
            self.assertNotIn("collectors/demo/README.md", system_prompt)
            self.assertNotIn("collectors/demo/missing.md", system_prompt)

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
            bot.llm = DummyLLM('{"skill_call": {"name": "echo", "args": {"text": "hello"}, "done": true}}')

            bot._handle_message(1, "run the echo skill")

        self.assertEqual(bot.telegram.sent, [(1, "echo:hello")])

    def test_skill_call_chain_requeries_until_done(self) -> None:
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
            bot.llm = SequentialLLM(
                [
                    '{"skill_call": {"name": "echo", "args": {"text": "step1"}, "done": false}}',
                    '{"skill_call": {"name": "echo", "args": {"text": "step2"}, "done": true}}',
                ]
            )

            bot._handle_message(1, "run multi-step")

            self.assertEqual(bot.telegram.sent, [(1, "echo:step2")])
            self.assertEqual(bot.llm.calls, 2)
            self.assertIn("Skill `echo` returned:\necho:step1", bot.llm.messages[-1]["content"])

    def test_web_search_chain_uses_full_html_once_then_summarizes_in_prompt_buffer(self) -> None:
        bot = self.make_bot()

        class InspectingLLM:
            def __init__(self) -> None:
                self.calls = []

            def generate_reply(self, messages):
                self.calls.append([m.copy() for m in messages])
                if len(self.calls) == 1:
                    return '{"skill_call": {"name": "web_search", "args": {"query": "x"}, "done": false}}'
                return "final"

        bot.llm = InspectingLLM()
        bot._run_skill_call = lambda *_args, **_kwargs: (
            "DuckDuckGo results for: x\n"
            "1. Example\n"
            "   URL: https://example.com\n"
            "   Snippet: Example snippet\n"
            "   HTML:\n"
            "<html><body>Huge page</body></html>"
        )

        prompt = [{"role": "system", "content": "sys"}, {"role": "user", "content": "find x"}]
        reply = bot._resolve_llm_reply(prompt, bot.llm.generate_reply(prompt))

        self.assertEqual(reply, "final")
        self.assertIn("<html><body>Huge page</body></html>", bot.llm.calls[1][-1]["content"])
        self.assertNotIn("<html><body>Huge page</body></html>", prompt[-1]["content"])
        self.assertIn("Snippet: Example snippet", prompt[-1]["content"])

    def test_handle_message_stores_web_search_summary_in_history(self) -> None:
        bot = self.make_bot()
        bot.telegram = DummyTelegram()
        bot.llm = SequentialLLM([
            '{"skill_call": {"name": "web_search", "args": {"query": "x"}}}',
            "Here is the summary from search.",
        ])
        bot._run_skill_call = lambda *_args, **_kwargs: (
            "DuckDuckGo results for: x\n"
            "1. Example\n"
            "   URL: https://example.com\n"
            "   Snippet: Example snippet\n"
            "   HTML:\n"
            "<html><body>Huge page</body></html>"
        )

        bot._handle_message(1, "search")

        self.assertEqual(bot.telegram.sent[0][1], "Here is the summary from search.")
        self.assertEqual(bot._history[1][1]["content"], "Here is the summary from search.")
        self.assertNotIn("<html><body>Huge page</body></html>", bot._history[1][1]["content"])

    def test_skill_call_without_done_requeries_for_long_output(self) -> None:
        bot = self.make_bot()
        bot.llm = SequentialLLM(
            [
                '{"skill_call": {"name": "web_search", "args": {"query": "x"}}}',
                "summarized",
            ]
        )
        bot._run_skill_call = lambda *_args, **_kwargs: "x" * 5000

        prompt = [{"role": "system", "content": "sys"}, {"role": "user", "content": "search"}]
        reply = bot._resolve_llm_reply(prompt, bot.llm.generate_reply(prompt))

        self.assertEqual(reply, "summarized")
        self.assertEqual(bot.llm.calls, 2)

    def test_skill_call_done_true_still_requeries_for_long_output(self) -> None:
        bot = self.make_bot()
        bot.llm = SequentialLLM(
            [
                '{"skill_call": {"name": "web_search", "args": {"query": "x"}, "done": true}}',
                "summarized",
            ]
        )
        bot._run_skill_call = lambda *_args, **_kwargs: "x" * 5000

        prompt = [{"role": "system", "content": "sys"}, {"role": "user", "content": "search"}]
        reply = bot._resolve_llm_reply(prompt, bot.llm.generate_reply(prompt))

        self.assertEqual(reply, "summarized")
        self.assertEqual(bot.llm.calls, 2)

    def test_skill_call_chain_logs_intermediate_prompt_and_response_on_debug(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "echo.py").write_text(
                'NAME = "echo"\nDESCRIPTION = "Echoes user text."\n\n'
                'def run(workspace, args):\n    return "echo:" + args.get("text", "")\n',
                encoding="utf-8",
            )

            runtime = RuntimeConfig(workspace_dir=td, skills_dir=str(skills_dir), debug=True)
            bot = self.make_bot(runtime=runtime)
            bot.telegram = DummyTelegram()
            bot.llm = SequentialLLM(
                [
                    '{"skill_call": {"name": "echo", "args": {"text": "step1"}, "done": false}}',
                    '{"skill_call": {"name": "echo", "args": {"text": "step2"}, "done": true}}',
                ]
            )

            with self.assertLogs(level="DEBUG") as logs:
                bot._handle_message(1, "run multi-step")

            self.assertTrue(any("Skill chain step 1/12 prompt before intermediate LLM call" in line for line in logs.output))
            self.assertTrue(any("Skill chain step 1/12 intermediate LLM response" in line for line in logs.output))

    def test_skill_call_parses_json_after_think_block(self) -> None:
        bot = self.make_bot()

        parsed = bot._try_parse_skill_call(
            """I will run that now.
{"skill_call": {"name": "taskwarrior", "args": {"action": "list"}}}"""
        )

        self.assertEqual(parsed, {"name": "taskwarrior", "args": {"action": "list"}, "done": False})

    def test_skill_call_parses_first_valid_json_block(self) -> None:
        bot = self.make_bot()

        parsed = bot._try_parse_skill_call(
            """Great idea.
{"note": "not a tool call"}
{"skill_call": {"name": "echo", "args": {"text": "hi"}}}"""
        )

        self.assertEqual(parsed, {"name": "echo", "args": {"text": "hi"}, "done": False})

    def test_skill_call_parses_top_level_tool_name_shape(self) -> None:
        bot = self.make_bot()

        parsed = bot._try_parse_skill_call(
            '{"web_search": {"name": "web_search", "args": {"query": "x"}, "done": false}}'
        )

        self.assertEqual(parsed, {"name": "web_search", "args": {"query": "x"}, "done": False})

    def test_skill_call_parses_top_level_tool_shape_without_args_key(self) -> None:
        bot = self.make_bot()

        parsed = bot._try_parse_skill_call(
            '{"web_search": {"query": "x", "max_results": 5, "done": true}}'
        )

        self.assertEqual(
            parsed,
            {
                "name": "web_search",
                "args": {"query": "x", "max_results": 5},
                "done": True,
            },
        )

    def test_skill_call_parses_web_search_args_without_name_or_done(self) -> None:
        bot = self.make_bot()

        parsed = bot._try_parse_skill_call(
            '{"web_search": {"query": "Howard Lutnick Epstein files role", "max_results": 10}}'
        )

        self.assertEqual(
            parsed,
            {
                "name": "web_search",
                "args": {"query": "Howard Lutnick Epstein files role", "max_results": 10},
                "done": False,
            },
        )

    def test_skill_call_parses_file_create_payload_without_name_or_done(self) -> None:
        bot = self.make_bot()

        parsed = bot._try_parse_skill_call(
            '{"file": {"action": "create", "paths": ["pharmacytodo"], "contents": ["deodorant"]}}'
        )

        self.assertEqual(
            parsed,
            {
                "name": "file",
                "args": {
                    "action": "create",
                    "paths": ["pharmacytodo"],
                    "contents": ["deodorant"],
                },
                "done": False,
            },
        )

    def test_skill_call_uses_nested_done_flag_inside_args(self) -> None:
        bot = self.make_bot()

        parsed = bot._try_parse_skill_call(
            '{"skill_call": {"name": "web_search", "args": {"query": "\\"Terrapin Ridge Farms\\" \\\"Scientology\\\" Mary O\'Donnell", "done": false}}}'
        )

        self.assertEqual(
            parsed,
            {
                "name": "web_search",
                "args": {"query": '"Terrapin Ridge Farms" "Scientology" Mary O\'Donnell'},
                "done": False,
            },
        )

    def test_skill_call_parse_short_circuits_when_skill_call_not_mentioned(self) -> None:
        bot = self.make_bot()
        decoder_path = "messaging_llm_bot.bot.json.JSONDecoder.raw_decode"

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

    def test_transcribe_voice_note_reads_first_non_empty_txt_when_name_differs(self) -> None:
        bot = self.make_bot(
            runtime=RuntimeConfig(
                enable_voice_notes=True,
                voice_transcribe_command=[
                    "python3",
                    "-c",
                    (
                        "import sys; from pathlib import Path; out = Path(sys.argv[1]); "
                        "(out / 'alt_name.txt').write_text('fallback transcript', encoding='utf-8')"
                    ),
                    "{input_dir}",
                ],
            )
        )
        bot.telegram = DummyTelegram()

        transcript = bot._transcribe_voice_note("voice-id")

        self.assertEqual(transcript, "fallback transcript")

    def test_transcribe_voice_note_reads_json_text_payload(self) -> None:
        bot = self.make_bot(
            runtime=RuntimeConfig(
                enable_voice_notes=True,
                voice_transcribe_command=[
                    "python3",
                    "-c",
                    (
                        "import json, sys; from pathlib import Path; out = Path(sys.argv[1]); "
                        "(out / 'transcript.json').write_text(json.dumps({'text': 'json transcript'}), encoding='utf-8')"
                    ),
                    "{input_dir}",
                ],
            )
        )
        bot.telegram = DummyTelegram()

        transcript = bot._transcribe_voice_note("voice-id")

        self.assertEqual(transcript, "json transcript")

    def test_transcribe_voice_file_path_reads_whisper_txt_output(self) -> None:
        bot = self.make_bot(
            runtime=RuntimeConfig(
                enable_voice_notes=True,
                voice_transcribe_command=[
                    "python3",
                    "-c",
                    (
                        "import sys; from pathlib import Path; p = Path(sys.argv[1]); "
                        "Path(sys.argv[2], p.stem + '.txt').write_text('signal txt transcript', encoding='utf-8')"
                    ),
                    "{input}",
                    "{input_dir}",
                ],
            )
        )

        with tempfile.TemporaryDirectory() as td:
            voice_path = Path(td) / "note.mp3"
            voice_path.write_bytes(b"voice")

            transcript = bot._transcribe_voice_file_path(str(voice_path))

        self.assertEqual(transcript, "signal txt transcript")

    def test_transcribe_voice_file_path_reads_json_text_payload(self) -> None:
        bot = self.make_bot(
            runtime=RuntimeConfig(
                enable_voice_notes=True,
                voice_transcribe_command=[
                    "python3",
                    "-c",
                    (
                        "import json, sys; from pathlib import Path; out = Path(sys.argv[1]); "
                        "(out / 'transcript.json').write_text(json.dumps({'text': 'signal json transcript'}), encoding='utf-8')"
                    ),
                    "{input_dir}",
                ],
            )
        )

        with tempfile.TemporaryDirectory() as td:
            voice_path = Path(td) / "note.mp3"
            voice_path.write_bytes(b"voice")

            transcript = bot._transcribe_voice_file_path(str(voice_path))

        self.assertEqual(transcript, "signal json transcript")

    def test_transcribe_voice_file_path_without_suffix_infers_mp3_input_name(self) -> None:
        bot = self.make_bot(
            runtime=RuntimeConfig(
                enable_voice_notes=True,
                voice_transcribe_command=[
                    "python3",
                    "-c",
                    (
                        "import sys; from pathlib import Path; p = Path(sys.argv[1]); "
                        "Path(sys.argv[2], p.stem + '.txt').write_text(p.suffix, encoding='utf-8')"
                    ),
                    "{input}",
                    "{input_dir}",
                ],
            )
        )

        with tempfile.TemporaryDirectory() as td:
            voice_path = Path(td) / "note_without_suffix"
            voice_path.write_bytes(b"ID3" + b"\x00" * 32)

            transcript = bot._transcribe_voice_file_path(str(voice_path))

        self.assertEqual(transcript, ".mp3")


    def test_handle_voice_update_uses_transcript(self) -> None:
        bot = self.make_bot(runtime=RuntimeConfig(enable_voice_notes=True, voice_transcribe_command=["cat", "{input}"]))
        bot.telegram = DummyTelegram()
        bot.llm = DummyLLM("heard")
        bot._transcribe_voice_note = lambda _fid: "turn on lights"

        bot._handle_update(IncomingMessage(update_id=1, chat_id=1, voice_file_id="voice-id"))

        self.assertEqual(bot.telegram.sent, [(1, "heard")])

    def test_handle_voice_update_logs_transcript_to_frontend_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime = RuntimeConfig(
                workspace_dir=td,
                enable_voice_notes=True,
                voice_transcribe_command=["cat", "{input}"],
            )
            bot = self.make_bot(runtime=runtime)
            bot.telegram = DummyTelegram()
            bot.llm = DummyLLM("heard")
            bot._transcribe_voice_note = lambda _fid: "turn on lights"

            bot._handle_update(IncomingMessage(update_id=1, chat_id=1, voice_file_id="voice-id"))

            telegram_history = Path(td) / "logs" / "telegram.history"
            events = [json.loads(line) for line in telegram_history.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[0]["direction"], "incoming")
            self.assertEqual(events[0]["text"], "[Voice note transcript]\nturn on lights")

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

    def test_poll_frontends_disables_signal_when_unavailable(self) -> None:
        bot = self.make_bot()
        bot.telegram = None
        bot.signal = PollingSignal(
            responses=[SignalFrontendUnavailableError("signal-cli missing"), []]
        )

        with self.assertLogs(level="WARNING") as logs:
            bot._poll_frontends_once()
            bot._poll_frontends_once()

        self.assertEqual(bot.signal.calls, 1)
        self.assertTrue(any("continuing with telegram-only frontend" in line for line in logs.output))

    def test_handle_voice_update_replies_on_transcription_error(self) -> None:
        bot = self.make_bot(runtime=RuntimeConfig(enable_voice_notes=True, voice_transcribe_command=["cat", "{input}"]))
        bot.telegram = DummyTelegram()
        bot.llm = DummyLLM("unused")

        def _boom(_fid: str):
            raise RuntimeError("bad")

        bot._transcribe_voice_note = _boom

        bot._handle_update(IncomingMessage(update_id=1, chat_id=1, voice_file_id="voice-id"))

        self.assertEqual(bot.telegram.sent, [(1, "I could not transcribe that voice note locally.")])

    def test_google_fi_sender_not_allowed_when_allowlist_is_configured(self) -> None:
        cfg = BotConfig(
            google_fi=GoogleFiConfig(
                account="default",
                allowed_sender_ids=["+15551112222"],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.google_fi = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_message("google_fi", "thread-1", "+15553334444", "hi")

        self.assertEqual(bot.llm.calls, 0)

    def test_google_fi_sender_allowed_when_number_format_differs(self) -> None:
        cfg = BotConfig(
            google_fi=GoogleFiConfig(
                account="default",
                allowed_sender_ids=["+1 (555) 111-2222"],
            ),
            llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
            runtime=RuntimeConfig(),
        )
        bot = BotRunner(cfg)
        bot.google_fi = object()
        bot._send_message = lambda backend, conversation_id, text: None
        bot.llm = DummyLLM("hello")

        bot._handle_message("google_fi", "thread-1", "+15551112222", "hi")

        self.assertEqual(bot.llm.calls, 1)

    def test_google_fi_unanswered_messages_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                google_fi=GoogleFiConfig(
                    account="default",
                    allowed_sender_ids=["+15551112222"],
                    store_unanswered_messages=True,
                ),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot.llm = DummyLLM("hello")

            update = IncomingMessage(
                update_id=1,
                backend="google_fi",
                conversation_id="thread-1",
                sender_id="+15553334444",
                text="collect me",
            )
            bot._handle_update(update)
            bot._handle_update(update)

            recent_lines = [
                line for line in (Path(td) / "google_fi.messages.recent").read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            self.assertEqual(len(recent_lines), 1)


    def test_google_fi_call_events_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                google_fi=GoogleFiConfig(
                    account="default",
                ),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)

            update = IncomingMessage(
                update_id=1,
                backend="google_fi",
                conversation_id="thread-1",
                sender_id="+15553334444",
                text="[Call event] ringing",
            )
            setattr(update, "event_type", "call")
            bot._handle_update(update)
            bot._handle_update(update)

            recent_lines = [
                line for line in (Path(td) / "google_fi.calls.recent").read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            self.assertEqual(len(recent_lines), 1)

    def test_google_fi_timeout_is_stored_as_unprocessed_message(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                google_fi=GoogleFiConfig(
                    account="default",
                    allowed_sender_ids=["+15550000000"],
                    store_unanswered_messages=True,
                ),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)
            bot._send_message = lambda backend, conversation_id, text: None
            bot.llm = TimeoutLLM()

            bot._handle_update(
                IncomingMessage(
                    update_id=1,
                    backend="google_fi",
                    conversation_id="thread-1",
                    sender_id="+15551112222",
                    text="request that times out",
                )
            )

            recent = (Path(td) / "google_fi.messages.recent").read_text(encoding="utf-8")
            self.assertIn("request that times out", recent)

    def test_google_fi_uses_sent_at_for_logged_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = BotConfig(
                google_fi=GoogleFiConfig(
                    account="default",
                    allowed_sender_ids=["+15550000000"],
                    store_unanswered_messages=True,
                ),
                llm=LLMConfig(base_url="u", api_key="k", model="m", history_messages=2),
                runtime=RuntimeConfig(workspace_dir=td),
            )
            bot = BotRunner(cfg)

            bot._handle_update(
                IncomingMessage(
                    update_id=1,
                    backend="google_fi",
                    conversation_id="thread-1",
                    sender_id="+15553334444",
                    text="collect me",
                    sent_at="2026-03-10T13:23:00+00:00",
                )
            )

            history_line = (Path(td) / "logs" / "google_fi.history").read_text(encoding="utf-8").splitlines()[0]
            recent_line = (Path(td) / "google_fi.messages.recent").read_text(encoding="utf-8").splitlines()[0]

            self.assertEqual(json.loads(history_line)["logged_at"], "2026-03-10T13:23:00+00:00")
            self.assertEqual(json.loads(recent_line)["logged_at"], "2026-03-10T13:23:00+00:00")


if __name__ == "__main__":
    unittest.main()
