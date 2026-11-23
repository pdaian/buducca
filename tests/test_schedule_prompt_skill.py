"""Scheduled prompt regressions run entirely in memory, without live services."""

import json
import threading
import unittest
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime
from unittest.mock import Mock, patch

from assistant_framework.action_runtime import ActionEnvelope
from assistant_framework.reminders import REMINDERS_FILE, ReminderStore, create_reminder_record, next_reminder_time
from assistant_framework.skills import Skill, SkillManager, build_skill_manifest
from assistant_framework.workspace import Workspace
from messaging_llm_bot.bot import BotRunner
from messaging_llm_bot.config import BotConfig, LLMConfig, RuntimeConfig, TelegramConfig
from messaging_llm_bot.http import RequestTimeoutError
from skills.schedule_prompt import build_action, run


def timestamp(value):
    return int(datetime.fromisoformat(value).timestamp())


NOW = timestamp("2026-01-01T00:00:00+00:00")
DUE = NOW + 60


class MemoryWorkspace(Workspace):
    def __init__(self):
        super().__init__("/__in_memory_scheduled_prompt_tests__")
        self.files = {}

    def read_text(self, path, default=""):
        return self.files.get(path, default)

    def write_text(self, path, content):
        self.files[path] = content

    def append_text(self, path, content):
        self.files[path] = self.read_text(path) + content


def prompt_args(**overrides):
    args = {"prompt": "Review my notes and send a plan", "unix_time": DUE, "backend": "telegram", "conversation_id": "123"}
    args.update(overrides)
    if "at" in overrides:
        args.pop("unix_time")
    return args


class SchedulePromptSkillTests(unittest.TestCase):
    def setUp(self):
        self.workspace = MemoryWorkspace()
        self.store = ReminderStore(self.workspace)
        clock = patch("skills.schedule_prompt.time.time", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def add(self, **overrides):
        return json.loads(run(self.workspace, prompt_args(**overrides)))["scheduled_prompt"]

    def test_discovery_exposes_schema_and_help(self):
        skill = SkillManager("skills").load()["schedule_prompt"]
        manifest = build_skill_manifest(skill)
        fields = {field["name"] for field in manifest["prompt_surface"]["args_schema_fields"]}
        self.assertEqual(fields, {"operation", "id", "prompt", "files", "at", "unix_time", "frequency", "timezone", "backend", "conversation_id"})
        self.assertIn("skill chains", manifest["human_help_surface"]["what_it_does"])
        self.assertIsNotNone(skill.build_action)

    def test_add_list_update_remove_and_reload(self):
        added = self.add(files=["notes.txt", "notes.txt", " other.txt "])
        self.assertEqual(added["delivery"], "prompt")
        self.assertNotIn("message", added)
        self.assertEqual(added["files"], ["notes.txt", "other.txt"])
        self.assertEqual(added["frequency"], "once")
        self.assertEqual(added["timezone"], "UTC")
        updated = json.loads(run(self.workspace, {
            "operation": "update", "id": added["id"], "prompt": "Review the latest notes", "frequency": "daily",
        }))["scheduled_prompt"]
        self.assertEqual(updated["id"], added["id"])
        self.assertEqual(updated["created_at"], added["created_at"])
        self.assertEqual(updated["unix_time"], added["unix_time"])
        self.assertEqual(updated["files"], added["files"])
        self.assertNotEqual(updated["revision"], added["revision"])
        self.assertEqual(ReminderStore(self.workspace).list(delivery="prompt"), [updated])
        self.assertEqual(json.loads(run(self.workspace, {"operation": "list"})), {"prompts": [updated]})
        self.assertIn(added["id"], run(self.workspace, {"operation": "remove", "id": added["id"]}))
        self.assertEqual(self.store.list(), [])

    def test_update_preserves_omitted_fields_and_can_clear_files_and_change_destination(self):
        added = self.add(files="notes.txt")
        updated = json.loads(run(self.workspace, {
            "operation": "update", "id": added["id"], "files": [], "at": "2026-02-02T10:45",
            "frequency": "weekly", "timezone": "America/New_York", "backend": "signal", "conversation_id": "+15550001",
        }))["scheduled_prompt"]
        self.assertEqual(updated["prompt"], added["prompt"])
        self.assertEqual(updated["files"], [])
        self.assertEqual(updated["backend"], "signal")
        self.assertEqual(updated["conversation_id"], "+15550001")
        self.assertEqual(updated["unix_time"], timestamp("2026-02-02T10:45:00-05:00"))
        self.assertEqual(next_reminder_time(updated, after=updated["unix_time"]), timestamp("2026-02-09T10:45:00-05:00"))

    def test_minute_rounding_and_dst_recurrence_use_reminder_rules(self):
        rounded = self.add(unix_time=DUE + 1)
        self.assertEqual(rounded["unix_time"], DUE + 60)
        daily = self.add(at="2026-03-07T09:15", timezone="America/New_York", frequency="daily")
        self.assertEqual(next_reminder_time(daily, after=daily["unix_time"]), timestamp("2026-03-08T09:15:00-04:00"))
        self.store.complete(daily, now=timestamp("2026-03-10T12:00:00-04:00"))
        advanced = next(record for record in self.store.list() if record["id"] == daily["id"])
        self.assertEqual(advanced["unix_time"], timestamp("2026-03-11T09:15:00-04:00"))
        self.assertEqual(advanced["anchor_time"], daily["anchor_time"])

    def test_direct_messages_cannot_be_managed_as_prompts(self):
        direct = self.store.add({"message": "Hello", "unix_time": DUE, "backend": "telegram", "conversation_id": "123"}, now=NOW)
        self.assertEqual(json.loads(run(self.workspace, {"operation": "list"})), {"prompts": []})
        self.assertIn("add_reminder", run(self.workspace, {"operation": "update", "id": direct["id"], "prompt": "Work"}))
        self.assertIn("not found", run(self.workspace, {"operation": "remove", "id": direct["id"]}))
        self.assertEqual(self.store.list(), [direct])
        prompt = self.add()
        with self.assertRaisesRegex(ValueError, "schedule_prompt"):
            self.store.update(prompt["id"], {"message": "Convert"}, now=NOW)

    def test_legacy_prompt_can_be_listed_edited_and_removed(self):
        legacy, error = create_reminder_record(prompt_args(files="notes.txt"))
        self.assertIsNone(error)
        self.workspace.append_text(REMINDERS_FILE, json.dumps(legacy) + "\n")
        self.assertEqual(json.loads(run(self.workspace, {"operation": "list"})), {"prompts": [legacy]})
        updated = json.loads(run(self.workspace, {"operation": "update", "id": legacy["id"], "prompt": "New work"}))["scheduled_prompt"]
        self.assertEqual(updated["id"], legacy["id"])
        self.assertEqual(updated["files"], ["notes.txt"])
        self.assertEqual(updated["delivery"], "prompt")
        self.assertIn(legacy["id"], run(self.workspace, {"operation": "remove", "id": legacy["id"]}))

    def test_invalid_arguments_never_write_a_schedule(self):
        invalid = [
            ({}, "prompt"), (prompt_args(prompt=" "), "prompt"), (prompt_args(prompt=None), "prompt"),
            (prompt_args(files=None), "files"), (prompt_args(files=[42]), "files"),
            (prompt_args(files="../outside.txt"), "escapes"),
            (prompt_args(unix_time=NOW), "future"), (prompt_args(unix_time=True), "integer"),
            (prompt_args(unix_time=10**100), "range"), (prompt_args(frequency="hourly"), "frequency"),
            (prompt_args(timezone="Bad/Zone"), "timezone"),
            (prompt_args(at="2026-03-08T02:30", timezone="America/New_York"), "gap"),
            (dict(prompt_args(), at="2026-01-02T09:00"), "not both"),
            (prompt_args(backend="email"), "backend"), (prompt_args(conversation_id=True), "conversation_id"),
            ({"prompt": "Work", "unix_time": DUE}, "backend"),
            ({"prompt": "Work", "unix_time": DUE, "backend": "telegram"}, "conversation_id"),
            ({"operation": "update"}, "id"), ({"operation": "update", "id": "unknown"}, "field to change"),
            ({"operation": "update", "id": "unknown", "prompt": "Work"}, "not found"),
            ({"operation": "remove", "id": "unknown", "prompt": "Work"}, "Remove accepts"),
            ({"operation": "list", "id": "unknown"}, "List accepts"),
            (prompt_args(message="Wrong skill"), "Unknown"), (prompt_args(id="manual"), "assigned"),
            ({"operation": "invalid"}, "operation"),
        ]
        for args, error in invalid:
            with self.subTest(args=args):
                self.assertIn(error, run(self.workspace, args))
        self.assertEqual(self.workspace.files, {})

    def test_invalid_update_does_not_change_record_and_completion_preserves_edits(self):
        original = self.add()
        before = dict(self.workspace.files)
        self.assertIn("escapes", run(self.workspace, {"operation": "update", "id": original["id"], "files": "../bad"}))
        self.assertEqual(self.workspace.files, before)
        added = self.add()
        updated = self.store.update(original["id"], {"prompt": "Edited"}, now=DUE, delivery="prompt")
        self.store.complete(original, now=DUE)
        self.assertEqual(self.store.list(), [updated, added])
        self.store.remove(updated["id"], delivery="prompt")
        self.store.complete(updated, now=DUE)
        self.assertEqual(self.store.list(), [added])

    def test_each_mutation_uses_the_action_policy(self):
        self.assertIsNone(build_action({"operation": "list"}))
        for operation in ("add", "update", "remove"):
            action = build_action({"operation": operation})
            self.assertEqual(action.name, f"schedule_prompt.{operation}")
            self.assertEqual(action.writes, [REMINDERS_FILE])
            self.assertTrue(action.requires_approval)


class ScheduledPromptExecutionTests(unittest.TestCase):
    def setUp(self):
        self.workspace = MemoryWorkspace()
        self.traces = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("messaging_llm_bot.bot.Workspace", return_value=self.workspace))
        self.stack.enter_context(patch(
            "messaging_llm_bot.bot.write_trace",
            side_effect=lambda _workspace, payload: self.traces.append(json.loads(json.dumps(payload))),
        ))
        self.bot = BotRunner(BotConfig(
            telegram=TelegramConfig(bot_token="test"),
            llm=LLMConfig(base_url="https://example.invalid/v1", api_key="test", model="test", system_prompt_timezone="America/New_York"),
            runtime=RuntimeConfig(workspace_dir=str(self.workspace.root), max_concurrent_requests=1),
        ))
        self.bot.telegram = Mock()
        self.bot._typing_indicator = Mock(side_effect=lambda *_args: nullcontext())
        self.bot._build_system_prompt = Mock(return_value="Test system prompt")
        self.bot.llm.http_client = Mock(spec=["post_json"])
        self.bot.llm.http_client.post_json.return_value = self.response("Finished work")
        self.store = ReminderStore(self.workspace)
        self.clock = self.stack.enter_context(patch("messaging_llm_bot.bot.time.time", return_value=DUE))

    @staticmethod
    def response(text):
        return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}

    @classmethod
    def call(cls, name, args=None):
        return cls.response(json.dumps({"skill_call": {"name": name, "args": args or {}, "done": False}}))

    def add(self, **overrides):
        return self.store.add(prompt_args(**overrides), now=NOW, delivery="prompt")

    def install_skill(self, skill):
        skills = dict(self.bot._skills, **{skill.name: skill})
        self.stack.enter_context(patch.object(self.bot, "_load_runtime_skills", return_value=skills))

    def test_chat_defaults_and_explicit_other_backend_require_destination(self):
        with self.bot._request_context("telegram:123"):
            self.bot._build_messages(123, "Schedule work", backend="telegram", conversation_id="123", sender_id="456")
            record = json.loads(self.bot._run_skill_call("schedule_prompt", {"prompt": "Work", "at": "2099-01-01T09:00"}))["scheduled_prompt"]
            invalid = self.bot._run_skill_call("schedule_prompt", {"prompt": "Work", "at": "2099-01-01T09:00", "backend": "signal"})
            listed = json.loads(self.bot._run_skill_call("schedule_prompt", {"operation": "list"}))["prompts"]
        self.assertEqual(record["backend"], "telegram")
        self.assertEqual(record["conversation_id"], "123")
        self.assertEqual(record["timezone"], "America/New_York")
        self.assertIn("conversation_id", invalid)
        self.assertEqual(listed, [record])
        self.assertEqual(self.bot._request_state.reminder_context, {})
        self.bot.llm.http_client.post_json.assert_not_called()

    def test_policy_denial_prevents_scheduling(self):
        with patch("messaging_llm_bot.bot.load_action_policy", return_value={"default": "deny"}):
            result = self.bot._run_skill_call("schedule_prompt", prompt_args(unix_time=DUE + 60))
        self.assertIn("Action denied", result)
        self.assertEqual(self.store.list(), [])

    def test_due_prompt_runs_a_full_skill_chain_and_delivers_the_result_once(self):
        self.workspace.write_text("notes.txt", "Original notes")
        read_notes = Mock(side_effect=lambda workspace, _args: workspace.read_text("notes.txt"))
        self.install_skill(Skill("read_notes", "Read notes", read_notes))
        record = self.add()
        future = self.add(unix_time=DUE + 600)
        self.bot._history[123].append({"role": "user", "content": "Keep the plan brief"})
        self.workspace.write_text("notes.txt", "Updated before execution")
        self.bot.llm.http_client.post_json.side_effect = [
            self.call("read_notes"),
            self.call("add_reminder", {"message": "Follow up", "unix_time": DUE + 3600}),
            self.response("Here is the plan"),
        ]
        self.bot._poll_due_reminders_once()
        self.bot._poll_due_reminders_once()
        read_notes.assert_called_once()
        self.assertEqual(self.bot.llm.http_client.post_json.call_count, 3)
        self.assertEqual(self.bot.telegram.send_message.call_count, 1)
        self.assertEqual(self.bot.telegram.send_message.call_args.args[0], 123)
        self.assertIn("Here is the plan", self.bot.telegram.send_message.call_args.args[1])
        remaining = self.store.list()
        self.assertNotIn(record["id"], {item["id"] for item in remaining})
        self.assertIn(future, remaining)
        direct = next(item for item in remaining if item.get("delivery") == "message")
        self.assertEqual(direct["conversation_id"], "123")
        self.assertEqual(direct["timezone"], "America/New_York")
        trace = self.traces[-1]
        self.assertEqual(trace["scheduled_prompt_id"], record["id"])
        self.assertEqual([step["skill_call"]["name"] for step in trace["steps"]], ["read_notes", "add_reminder"])
        self.assertEqual(trace["steps"][0]["skill_result"], "Updated before execution")
        self.assertEqual(trace["metrics"]["skill_chain_length"], 2)
        self.assertEqual(trace["last_prompt"][1]["content"], "Keep the plan brief")
        self.assertEqual(self.bot._history[123][-1]["content"], "Here is the plan")
        self.assertIn(record["id"], self.workspace.read_text("logs/reminders.history"))

    def test_future_prompts_and_direct_messages_do_not_start_a_model_turn(self):
        future = self.add(unix_time=DUE + 60)
        direct = self.store.add({"message": "Hello", "unix_time": DUE, "backend": "telegram", "conversation_id": "123"}, now=NOW)
        self.bot._poll_due_reminders_once()
        self.bot.llm.http_client.post_json.assert_not_called()
        self.bot.telegram.send_message.assert_not_called()
        self.assertEqual(self.store.list(), [direct, future])

    def test_overdue_recurring_prompt_runs_once_then_advances(self):
        record = self.add(frequency="weekly")
        self.clock.return_value = DUE + 20 * 86400
        self.bot._poll_due_reminders_once()
        self.bot._poll_due_reminders_once()
        updated = self.store.list()[0]
        self.assertEqual(updated["id"], record["id"])
        self.assertEqual(updated["unix_time"], DUE + 21 * 86400)
        self.bot.llm.http_client.post_json.assert_called_once()

    def test_failed_execution_stays_pending_with_a_minute_backoff(self):
        record = self.add()
        self.bot._generate_initial_model_reply = Mock(side_effect=[RequestTimeoutError("offline"), "Recovered"])
        with self.assertLogs(level="ERROR"):
            self.bot._poll_due_reminders_once()
        self.bot._poll_due_reminders_once()
        self.assertEqual(self.bot._generate_initial_model_reply.call_count, 1)
        self.assertEqual(self.store.list(), [record])
        self.bot.telegram.send_message.assert_not_called()
        self.assertEqual(self.traces[-1]["error"], "timeout")
        self.clock.return_value = DUE + 60
        self.bot._poll_due_reminders_once()
        self.assertEqual(self.store.list(), [])
        self.bot.telegram.send_message.assert_called_once_with(123, "Recovered")

    def test_edit_clears_failed_attempt_backoff(self):
        record = self.add()
        self.bot._generate_initial_model_reply = Mock(side_effect=[RuntimeError("offline"), "Recovered"])
        with self.assertLogs(level="ERROR"):
            self.bot._poll_due_reminders_once()
        self.store.update(record["id"], {"prompt": "Fixed work"}, now=DUE, delivery="prompt")
        self.bot._poll_due_reminders_once()
        self.assertEqual(self.bot._generate_initial_model_reply.call_count, 2)
        self.assertEqual(self.store.list(), [])

    def test_failed_delivery_does_not_consume_the_prompt_or_add_history(self):
        record = self.add()
        self.bot.telegram.send_message.side_effect = RuntimeError("offline")
        with self.assertLogs(level="ERROR"):
            self.bot._poll_due_reminders_once()
        self.assertEqual(self.store.list(), [record])
        self.assertEqual(list(self.bot._history[123]), [])
        self.assertEqual(self.traces[-1]["error"], "delivery_failed")
        self.bot._poll_due_reminders_once()
        self.bot.llm.http_client.post_json.assert_called_once()

    def test_read_only_and_unconfigured_destinations_do_not_execute_or_consume_work(self):
        record = self.add()
        self.bot.config.telegram.read_only = True
        self.bot._poll_due_reminders_once()
        self.assertEqual(self.store.list(), [record])
        self.bot.config.telegram.read_only = False
        self.store.update(record["id"], {"backend": "signal", "conversation_id": "+15550001"}, now=DUE, delivery="prompt")
        with self.assertLogs(level="WARNING"):
            self.bot._poll_due_reminders_once()
        self.assertEqual(len(self.store.list()), 1)
        self.bot.llm.http_client.post_json.assert_not_called()
        self.bot.telegram.send_message.assert_not_called()

    def test_request_budget_stops_the_chain_and_records_completed_work(self):
        record = self.add()
        skill_run = Mock(return_value="Skill result")
        self.install_skill(Skill("work", "Do work", skill_run))
        self.bot.config.runtime.max_llm_requests_per_turn = 1
        self.bot.llm.http_client.post_json.return_value = self.call("work")
        with self.assertLogs(level="ERROR"):
            self.bot._poll_due_reminders_once()
        self.bot.llm.http_client.post_json.assert_called_once()
        skill_run.assert_called_once()
        self.assertEqual(self.store.list(), [record])
        self.assertEqual(self.traces[-1]["metrics"]["stop_reason"], "max_llm_requests_per_turn")
        self.assertEqual(self.traces[-1]["steps"][0]["skill_result"], "Skill result")
        self.bot.telegram.send_message.assert_not_called()

    def test_action_policy_is_checked_again_when_the_prompt_executes(self):
        self.add()
        skill_run = Mock(return_value="Changed state")
        action = ActionEnvelope(name="work.write", args={}, reason="test", writes=["notes.txt"])
        self.install_skill(Skill("work", "Do work", skill_run, build_action=lambda _args: action))
        self.bot.llm.http_client.post_json.side_effect = [self.call("work"), self.response("The action was denied")]
        with patch("messaging_llm_bot.bot.load_action_policy", return_value={"default": "deny"}):
            self.bot._poll_due_reminders_once()
        skill_run.assert_not_called()
        self.assertIn("Action denied", self.traces[-1]["steps"][0]["skill_result"])
        self.assertEqual(self.store.list(), [])

    def test_model_directives_apply_to_the_scheduled_turn(self):
        self.add(prompt="/1 /nothink Review my notes")
        force_runner = self.stack.enter_context(patch.object(self.bot.llm, "force_runner", return_value=nullcontext()))
        generate = self.stack.enter_context(patch.object(self.bot, "_generate_initial_model_reply", return_value="Finished"))
        self.bot._poll_due_reminders_once()
        force_runner.assert_called_once_with(1)
        self.assertTrue(generate.call_args.kwargs["disable_thinking"])
        self.assertIn("Review my notes", generate.call_args.args[0][-1]["content"])
        self.assertNotIn("/nothink", generate.call_args.args[0][-1]["content"])

    def test_legacy_prompt_still_executes_with_its_nothink_directive(self):
        legacy, _ = create_reminder_record(prompt_args(prompt="Review my notes /nothink"))
        self.workspace.append_text(REMINDERS_FILE, json.dumps(legacy) + "\n")
        with patch.object(self.bot, "_generate_initial_model_reply", return_value="Finished") as generate:
            self.bot._poll_due_reminders_once()
        self.assertTrue(generate.call_args.kwargs["disable_thinking"])
        self.assertEqual(self.store.list(), [])

    def test_file_context_is_read_at_execution_and_cannot_escape_workspace(self):
        record = self.add(files=["notes.txt", "missing.txt"])
        self.workspace.write_text("notes.txt", "Notes changed after scheduling")
        resolve = self.workspace.resolve

        def resolve_file(path):
            if path == "notes.txt":
                return Mock(exists=lambda: True, read_text=lambda **_kwargs: self.workspace.read_text(path))
            return resolve(path)

        with patch.object(self.workspace, "resolve", side_effect=resolve_file):
            self.bot._poll_due_reminders_once()
        prompt = self.traces[-1]["last_message"]
        self.assertIn("Notes changed after scheduling", prompt)
        self.assertIn("File `missing.txt` is missing", prompt)
        record["files"] = ["../outside.txt"]
        self.assertIn("escapes the workspace", self.bot._build_due_reminder_text(record))

    def test_unreadable_file_is_reported_without_failing_the_model_turn(self):
        self.add(files=["notes.txt"])
        resolve = self.workspace.resolve

        def resolve_file(path):
            if path == "notes.txt":
                return Mock(exists=lambda: True, read_text=Mock(side_effect=PermissionError("unreadable")))
            return resolve(path)

        with patch.object(self.workspace, "resolve", side_effect=resolve_file):
            self.bot._poll_due_reminders_once()
        self.assertIn("File `notes.txt` could not be read", self.traces[-1]["last_message"])
        self.assertEqual(self.store.list(), [])

    def test_removal_while_waiting_for_request_capacity_prevents_execution(self):
        record = self.add()
        waiting = threading.Event()
        request_context = self.bot._request_context
        errors = []

        @contextmanager
        def wait_for_capacity(scope_key):
            waiting.set()
            with request_context(scope_key):
                yield

        def poll():
            try:
                self.bot._poll_due_reminders_once()
            except BaseException as exc:
                errors.append(exc)

        with patch.object(self.bot, "_request_context", side_effect=wait_for_capacity):
            self.bot._request_slots.acquire()
            worker = threading.Thread(target=poll)
            try:
                worker.start()
                self.assertTrue(waiting.wait(timeout=2))
                self.store.remove(record["id"], delivery="prompt")
            finally:
                self.bot._request_slots.release()
                worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.bot.llm.http_client.post_json.assert_not_called()
        self.assertEqual(self.store.list(), [])

    def test_completion_does_not_remove_a_schedule_edited_during_execution(self):
        record = self.add()
        updated = []

        def edit(_prompt, **_kwargs):
            updated.append(self.store.update(record["id"], {"prompt": "Next work"}, now=DUE, delivery="prompt"))
            return "Finished original work"

        with patch.object(self.bot, "_generate_initial_model_reply", side_effect=edit):
            self.bot._poll_due_reminders_once()
        self.assertEqual(self.store.list(), updated)
