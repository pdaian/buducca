"""Reminder regressions use an in-memory workspace and create no runtime files."""

import asyncio
import json
import threading
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from assistant_framework.reminders import (
    REMINDERS_FILE,
    ReminderStore,
    create_reminder_record,
    next_reminder_time,
    prepare_message_reminder,
    serialize_reminder_record,
)
from assistant_framework.skills import SkillManager
from assistant_framework.telegram_user_client_base import BaseTelegramUserClient
from assistant_framework.workspace import Workspace
from messaging_llm_bot.bot import BotRunner
from messaging_llm_bot.config import BotConfig, LLMConfig, RuntimeConfig, TelegramConfig
from messaging_llm_bot.reminder_service import ReminderService
from messaging_llm_bot.telegram_user_client import TelegramUserClient
from skills.add_reminder import build_action, run


def timestamp(value):
    return int(datetime.fromisoformat(value).timestamp())


NOW = timestamp("2026-01-01T00:00:00+00:00")
DUE = NOW + 60


class MemoryWorkspace(Workspace):
    def __init__(self):
        super().__init__("/__in_memory_reminder_tests__")
        self.files = {}

    def read_text(self, path, default=""):
        return self.files.get(path, default)

    def append_text(self, path, content):
        self.files[path] = self.read_text(path) + content

    def write_text(self, path, content):
        self.files[path] = content


def reminder_args(**overrides):
    return dict(message="Take a break", unix_time=DUE, backend="telegram", conversation_id="123", **overrides)


class ReminderScheduleTests(unittest.TestCase):
    def make_record(self, **overrides):
        args = reminder_args()
        args.update(overrides)
        if "at" in overrides:
            args.pop("unix_time")
        return prepare_message_reminder(args, now=NOW)

    def test_once_and_minute_rounding(self):
        record = self.make_record(unix_time=DUE + 1)
        self.assertEqual(record["unix_time"], DUE + 60)
        self.assertIsNone(next_reminder_time(record, after=DUE + 60))
        self.assertEqual(record["frequency"], "once")
        fractional = self.make_record(at="2026-01-01T00:01:00.001+00:00")
        self.assertEqual(fractional["unix_time"], DUE + 60)

    def test_daily_and_weekly_skip_missed_occurrences(self):
        for frequency, expected in (("daily", "2026-01-23T09:15:00+00:00"), ("weekly", "2026-01-29T09:15:00+00:00")):
            with self.subTest(frequency=frequency):
                record = self.make_record(at="2026-01-01T09:15", frequency=frequency)
                self.assertEqual(next_reminder_time(record, after=timestamp("2026-01-22T08:00:00+00:00")), timestamp("2026-01-22T09:15:00+00:00"))
                self.assertEqual(next_reminder_time(record, after=timestamp("2026-01-22T09:15:00+00:00")), timestamp(expected))

    def test_daily_recurrence_preserves_local_time_across_dst(self):
        record = self.make_record(at="2026-03-07T09:15", frequency="daily", timezone="America/New_York")
        self.assertEqual(next_reminder_time(record, after=record["unix_time"]), timestamp("2026-03-08T09:15:00-04:00"))

    def test_dst_gap_shifts_one_occurrence_without_drifting(self):
        record = self.make_record(at="2026-03-07T02:30", frequency="daily", timezone="America/New_York")
        next_time = next_reminder_time(record, after=record["unix_time"])
        self.assertEqual(next_time, timestamp("2026-03-08T03:30:00-04:00"))
        self.assertEqual(next_reminder_time(record, after=next_time), timestamp("2026-03-09T02:30:00-04:00"))

    def test_dst_fold_only_fires_once_and_weekly_keeps_weekday(self):
        record = self.make_record(at="2026-10-25T01:30", frequency="weekly", timezone="America/New_York")
        first_fold = timestamp("2026-11-01T01:30:00-04:00")
        self.assertEqual(next_reminder_time(record, after=record["unix_time"]), first_fold)
        self.assertEqual(next_reminder_time(record, after=first_fold), timestamp("2026-11-08T01:30:00-05:00"))

    def test_ambiguous_start_accepts_an_explicit_offset(self):
        record = self.make_record(at="2026-11-01T01:30:00-05:00", timezone="America/New_York")
        self.assertEqual(record["unix_time"], timestamp("2026-11-01T01:30:00-05:00"))

    def test_invalid_inputs_are_rejected(self):
        for changes in (
            {"frequency": "hourly"}, {"timezone": "Not/AZone"}, {"timezone": None},
            {"message": " "}, {"message": None}, {"backend": "email"},
            {"conversation_id": True}, {"conversation_id": "invalid"},
            {"unix_time": True}, {"unix_time": NOW}, {"unix_time": 10**100},
            {"at": "not a date"}, {"at": "2026-01-02"}, {"at": "2026-03-08T02:30", "timezone": "America/New_York"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.make_record(**changes)


class ReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self.workspace = MemoryWorkspace()
        self.store = ReminderStore(self.workspace)

    def test_add_list_update_remove_and_reload(self):
        record = self.store.add(reminder_args(), now=NOW)
        other = ReminderStore(self.workspace)
        self.assertEqual(other.list(), [record])
        updated = other.update(record["id"], {"message": "Drink water", "frequency": "daily"}, now=NOW)
        self.assertEqual(updated["id"], record["id"])
        self.assertEqual(updated["created_at"], record["created_at"])
        self.assertEqual(updated["unix_time"], DUE)
        self.assertNotEqual(updated["revision"], record["revision"])
        self.assertEqual(self.store.list(), [updated])
        other.remove(record["id"])
        self.assertEqual(ReminderStore(self.workspace).list(), [])

    def test_success_removes_once_and_advances_recurring(self):
        once = self.store.add(reminder_args(), now=NOW)
        daily = self.store.add(reminder_args(frequency="daily"), now=NOW)
        weekly = self.store.add(reminder_args(frequency="weekly"), now=NOW)
        for record in (once, daily, weekly):
            self.store.complete(record, now=DUE + 10 * 86400)
        records = {record["id"]: record for record in self.store.list()}
        self.assertNotIn(once["id"], records)
        self.assertEqual(records[daily["id"]]["unix_time"], DUE + 11 * 86400)
        self.assertEqual(records[weekly["id"]]["unix_time"], DUE + 14 * 86400)
        self.assertEqual(records[daily["id"]]["anchor_time"], DUE)

    def test_completion_preserves_concurrent_add_edit_and_removal(self):
        original = self.store.add(reminder_args(), now=NOW)
        added = self.store.add(reminder_args(), now=NOW)
        updated = self.store.update(original["id"], {"message": "Edited"}, now=DUE)
        self.store.complete(original, now=DUE)
        self.assertEqual(self.store.list(), [updated, added])
        self.store.remove(original["id"])
        self.store.complete(updated, now=DUE)
        self.assertEqual(self.store.list(), [added])

    def test_concurrent_additions_are_not_lost(self):
        barrier = threading.Barrier(9)

        def add():
            barrier.wait(timeout=2)
            self.store.add(reminder_args(), now=NOW)

        workers = [threading.Thread(target=add) for _ in range(8)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=2)
        for worker in workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len({record["id"] for record in self.store.list()}), 8)

    def test_malformed_lines_do_not_hide_valid_records(self):
        self.workspace.write_text(REMINDERS_FILE, 'bad json\n[]\n{"id":"bad","unix_time":true}\n{"partial":')
        valid = self.store.add(reminder_args(), now=NOW)
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.store.list(), [valid])

    def test_legacy_prompt_records_survive_and_complete(self):
        record, error = create_reminder_record({"unix_time": DUE, "prompt": "Summarize my notes", "backend": "telegram", "conversation_id": "123"})
        self.assertIsNone(error)
        self.workspace.write_text(REMINDERS_FILE, serialize_reminder_record(record) + "\n")
        direct = self.store.add(reminder_args(), now=NOW)
        self.store.complete(record, now=DUE)
        self.assertEqual(self.store.list(), [direct])

    def test_invalid_update_and_missing_id_do_not_mutate_storage(self):
        record = self.store.add(reminder_args(), now=NOW)
        before = dict(self.workspace.files)
        with self.assertRaises(ValueError):
            self.store.update(record["id"], {"frequency": "invalid"}, now=NOW)
        with self.assertRaises(ValueError):
            self.store.update("missing", {"message": "update"}, now=NOW)
        with self.assertRaises(ValueError):
            self.store.remove("missing")
        self.assertEqual(self.workspace.files, before)

    def test_rescheduling_changes_time_zone_frequency_and_destination_with_same_id(self):
        original = self.store.add(reminder_args(), now=NOW)
        updated = self.store.update(original["id"], {
            "at": "2026-02-02T10:45", "timezone": "America/New_York", "frequency": "weekly",
            "backend": "signal", "conversation_id": "+15550001",
        }, now=NOW)
        self.assertEqual(updated["id"], original["id"])
        self.assertEqual(updated["unix_time"], timestamp("2026-02-02T10:45:00-05:00"))
        self.assertEqual(updated["anchor_time"], updated["unix_time"])
        self.assertEqual(updated["backend"], "signal")
        self.assertEqual(updated["conversation_id"], "+15550001")
        self.assertEqual(next_reminder_time(updated, after=updated["unix_time"]), timestamp("2026-02-09T10:45:00-05:00"))


class ReminderSkillTests(unittest.TestCase):
    def test_skill_is_discovered_with_schema_and_help(self):
        skill = SkillManager("skills").load()["add_reminder"]
        self.assertIn("weekly", skill.args_schema)
        self.assertIn("independent", skill.description)

    def test_all_skill_operations_return_the_same_id(self):
        workspace = MemoryWorkspace()
        with patch("skills.add_reminder.time.time", return_value=NOW):
            added = json.loads(run(workspace, reminder_args()))["reminder"]
            updated = json.loads(run(workspace, {"operation": "update", "id": added["id"], "message": "New text"}))["reminder"]
        self.assertEqual(added["id"], updated["id"])
        self.assertEqual(json.loads(run(workspace, {"operation": "list"}))["reminders"], [updated])
        self.assertIn(added["id"], run(workspace, {"operation": "remove", "id": added["id"]}))
        self.assertEqual(json.loads(run(workspace, {"operation": "list"})), {"reminders": []})

    def test_skill_rejects_ambiguous_or_unknown_fields(self):
        workspace = MemoryWorkspace()
        for args, error in (
            ({"operation": "update"}, "`id`"),
            ({"operation": "update", "id": "x"}, "field to change"),
            ({"operation": "list", "id": "x"}, "List accepts"),
            ({"operation": "remove", "id": "x", "message": "ignored"}, "Remove accepts"),
            ({"operation": "invalid"}, "operation"),
            ({"id": "x"}, "assigned"),
            ({"repeat": "daily"}, "Unknown"),
            (dict(reminder_args(), at="2026-01-02T00:00"), "not both"),
        ):
            with self.subTest(args=args):
                self.assertIn(error, run(workspace, args))
        self.assertEqual(workspace.files, {})

    def test_action_policy_can_control_each_mutation(self):
        self.assertIsNone(build_action({"operation": "list"}))
        for operation in ("add", "update", "remove"):
            action = build_action({"operation": operation})
            self.assertEqual(action.name, f"add_reminder.{operation}")
            self.assertEqual(action.writes, [REMINDERS_FILE])


class ReminderServiceTests(unittest.TestCase):
    def setUp(self):
        self.workspace = MemoryWorkspace()
        self.store = ReminderStore(self.workspace)
        self.clock = DUE
        self.send = Mock()
        self.service = ReminderService(self.store, self.send, clock=lambda: self.clock)
        self.addCleanup(self.service.stop)

    def wait_idle(self):
        with self.service._lock:
            workers = list(self.service._active.values())
        for worker in workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())

    def test_only_due_direct_messages_are_delivered_once(self):
        record = self.store.add(reminder_args(), now=NOW)
        future_args = dict(reminder_args(), unix_time=DUE + 60)
        future = self.store.add(future_args, now=NOW)
        legacy, _ = create_reminder_record({"unix_time": DUE, "prompt": "Legacy", "backend": "telegram", "conversation_id": "123"})
        self.workspace.append_text(REMINDERS_FILE, json.dumps(legacy) + "\n")
        self.service.poll_once()
        self.wait_idle()
        self.service.poll_once()
        self.wait_idle()
        self.send.assert_called_once_with(record)
        self.assertEqual({item["id"] for item in self.store.list()}, {future["id"], legacy["id"]})

    def test_failure_retries_after_a_minute_without_consuming_reminder(self):
        record = self.store.add(reminder_args(), now=NOW)
        self.send.side_effect = RuntimeError("offline")
        with self.assertLogs(level="ERROR"):
            self.service.poll_once()
            self.wait_idle()
        self.assertEqual(self.store.list(), [record])
        self.service.poll_once()
        self.wait_idle()
        self.assertEqual(self.send.call_count, 1)
        self.clock += 60
        self.send.side_effect = None
        self.service.poll_once()
        self.wait_idle()
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.store.list(), [])

    def test_blocked_send_does_not_block_other_reminders_or_duplicate_itself(self):
        blocked = self.store.add(reminder_args(), now=NOW)
        entered, release, sent = threading.Event(), threading.Event(), threading.Event()

        def send(record):
            if record["id"] == blocked["id"]:
                entered.set()
                if not release.wait(timeout=3):
                    raise AssertionError("test did not release blocked sender")
            else:
                sent.set()

        self.send.side_effect = send
        try:
            self.service.poll_once()
            self.assertTrue(entered.wait(timeout=2))
            self.store.add(reminder_args(), now=NOW)
            self.service.poll_once()
            self.assertTrue(sent.wait(timeout=2))
            self.assertEqual(self.send.call_count, 2)
        finally:
            release.set()
            self.wait_idle()

    def test_removal_during_delivery_is_not_resurrected(self):
        record = self.store.add(reminder_args(frequency="daily"), now=NOW)
        self.send.side_effect = lambda _: self.store.remove(record["id"])
        self.service.poll_once()
        self.wait_idle()
        self.assertEqual(self.store.list(), [])

    def test_service_starts_once_and_stops_promptly(self):
        self.service.start()
        thread = self.service._thread
        self.service.start()
        self.assertIs(self.service._thread, thread)
        self.service.stop()
        self.assertFalse(thread.is_alive())

    def test_worker_waits_until_the_next_minute_boundary(self):
        self.clock = DUE + 17.25
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        with patch.object(self.service, "_stop", stop), patch.object(self.service, "poll_once") as poll:
            self.service._run()
        poll.assert_called_once()
        stop.wait.assert_called_once_with(42.75)


class ReminderBotTests(unittest.TestCase):
    def make_bot(self):
        workspace = MemoryWorkspace()
        config = BotConfig(
            telegram=TelegramConfig(bot_token="test"),
            llm=LLMConfig(base_url="unused", api_key="test", model="test"),
            runtime=RuntimeConfig(workspace_dir=str(workspace.root), max_concurrent_requests=1),
        )
        with patch("messaging_llm_bot.bot.Workspace", return_value=workspace):
            bot = BotRunner(config)
        bot.telegram = Mock()
        # Traces intentionally live outside Workspace.write_text; keep these
        # integration tests entirely in memory as well.
        bot._write_message_trace = Mock()
        return bot

    def test_harness_add_defaults_to_current_conversation_and_timezone(self):
        bot = self.make_bot()
        with bot._request_context("telegram:123"):
            with patch.object(bot, "_build_system_prompt", return_value="Test prompt"):
                bot._build_messages(123, "remind me", backend="telegram", conversation_id="123", sender_id="123")
            result = json.loads(bot._run_skill_call("add_reminder", {"message": "Hello", "at": "2099-01-01T09:00"}))
        record = result["reminder"]
        self.assertEqual(record["backend"], "telegram")
        self.assertEqual(record["conversation_id"], "123")
        self.assertEqual(record["timezone"], bot.config.llm.system_prompt_timezone)
        self.assertEqual(bot._request_state.reminder_context, {})

    def test_explicit_other_backend_does_not_inherit_current_recipient(self):
        bot = self.make_bot()
        bot._request_state.reminder_context = {"backend": "telegram", "conversation_id": "123"}
        result = bot._run_skill_call("add_reminder", {"message": "Hello", "at": "2099-01-01T09:00", "backend": "signal"})
        self.assertIn("conversation_id", result)
        self.assertEqual(bot._reminder_service.store.list(), [])

    def test_action_policy_denial_prevents_scheduling(self):
        bot = self.make_bot()
        with patch("messaging_llm_bot.bot.load_action_policy", return_value={"default": "deny"}):
            result = bot._run_skill_call("add_reminder", reminder_args())
        self.assertIn("Action denied", result)
        self.assertEqual(bot._reminder_service.store.list(), [])

    def test_delivery_while_chat_holds_conversation_lock_and_only_request_slot(self):
        bot = self.make_bot()
        entered, release, delivered = threading.Event(), threading.Event(), threading.Event()

        class BlockingLLM:
            calls = 0

            def generate_reply(self, messages, *, disable_thinking=False):
                self.calls += 1
                entered.set()
                if not release.wait(timeout=5):
                    raise AssertionError("test did not release model request")
                return "Chat finished"

        bot.llm = BlockingLLM()
        bot.telegram.send_message.side_effect = lambda recipient, text: delivered.set() if text.startswith("[Reminder ") else None
        bot._reminder_service.store.add(reminder_args(), now=NOW)
        bot._reminder_service.clock = lambda: DUE
        errors = []

        def handle():
            try:
                bot._handle_message(123, "Hello")
            except BaseException as exc:
                errors.append(exc)

        chat = threading.Thread(target=handle)
        try:
            chat.start()
            self.assertTrue(entered.wait(timeout=2))
            bot._reminder_service.start()
            self.assertTrue(delivered.wait(timeout=2))
            self.assertTrue(chat.is_alive())
            self.assertEqual(bot.llm.calls, 1)
        finally:
            release.set()
            chat.join(timeout=2)
            bot._reminder_service.stop()
        self.assertFalse(chat.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(bot._reminder_service.store.list(), [])

    def test_read_only_backend_does_not_consume_a_reminder(self):
        bot = self.make_bot()
        bot.config.telegram.read_only = True
        record = bot._reminder_service.store.add(reminder_args(), now=NOW)
        with self.assertRaisesRegex(RuntimeError, "read-only"):
            bot._send_scheduled_message(record)
        bot.telegram.send_message.assert_not_called()
        self.assertEqual(bot._reminder_service.store.list(), [record])

    def test_run_forever_manages_reminder_service_lifecycle(self):
        bot = self.make_bot()
        with patch.object(bot._reminder_service, "start") as start, patch.object(bot._reminder_service, "stop") as stop:
            with patch.object(bot, "_start_frontend_workers"), patch.object(bot, "_poll_due_structured_schedule_once", side_effect=KeyboardInterrupt):
                bot.run_forever()
        start.assert_called_once()
        stop.assert_called_once()


class TelegramReminderConcurrencyTests(unittest.TestCase):
    def setUp(self):
        new_loop = asyncio.new_event_loop

        def timer_driven_loop():
            loop = new_loop()

            def tick():
                loop.call_later(0.01, tick)

            # Keep the fake transport loop active without socket I/O. Some test
            # sandboxes deny the selector's cross-thread socket wake-up writes.
            loop.call_soon(tick)
            return loop

        patcher = patch("asyncio.new_event_loop", side_effect=timer_driven_loop)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_event_loop_closes_and_can_restart(self):
        client = BaseTelegramUserClient(1, "test", "/__unused_reminder_session__", error_prefix="test")

        async def answer():
            return 42

        try:
            self.assertEqual(client._run(answer()), 42)
            old_loop, old_thread = client._loop, client._loop_thread
            client.close()
            self.assertTrue(old_loop.is_closed())
            self.assertFalse(old_thread.is_alive())
            self.assertEqual(client._run(answer()), 42)
            self.assertIsNot(client._loop, old_loop)
        finally:
            client.close()

    def test_send_proceeds_while_telegram_user_poll_is_pending(self):
        client = TelegramUserClient(api_id=1, api_hash="test", session_path="/__unused_reminder_session__")
        entered, sent = threading.Event(), threading.Event()
        loop_gate = []
        errors = []

        class FakeTelegram:
            connected = False
            connects = 0

            def is_connected(self):
                return self.connected

            async def connect(self):
                self.connects += 1
                await asyncio.sleep(0)
                self.connected = True

            async def is_user_authorized(self):
                return True

            async def iter_dialogs(self, **kwargs):
                gate = asyncio.Event()
                loop_gate.append(gate)
                entered.set()
                await gate.wait()
                if False:
                    yield None

            async def send_message(self, chat_id, text):
                sent.set()

            async def disconnect(self):
                self.connected = False

        fake = FakeTelegram()
        client._ensure_client = lambda: fake

        def poll():
            try:
                client.get_updates()
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=poll)
        try:
            worker.start()
            self.assertTrue(entered.wait(timeout=2))
            client.send_message(123, "Reminder")
            self.assertTrue(sent.is_set())
            self.assertTrue(worker.is_alive())
            self.assertEqual(fake.connects, 1)
        finally:
            if loop_gate:
                client._loop.call_soon_threadsafe(loop_gate[0].set)
            worker.join(timeout=2)
            client.close()
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertIsNone(client._loop)
        self.assertFalse(fake.connected)


if __name__ == "__main__":
    unittest.main()
