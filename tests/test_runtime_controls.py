import json
import threading
import unittest
from contextlib import ExitStack, nullcontext
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from assistant_framework.skills import Skill
from messaging_llm_bot.bot import BotRunner
from messaging_llm_bot.config import BotConfig, LLMConfig, RuntimeConfig, TelegramConfig


class RuntimeControlTests(unittest.TestCase):
    """Exercise the actual model loop with transport and persistence in memory."""

    def setUp(self) -> None:
        self.traces = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch(
            "messaging_llm_bot.bot.write_trace",
            side_effect=lambda _workspace, payload: self.traces.append(json.loads(json.dumps(payload))),
        ))
        for method in ("_normalize_recent_message_files", "_load_unanswered_recent_keys", "_load_last_hourly_slot"):
            stack.enter_context(patch.object(BotRunner, method, return_value=None))
        self.skill = Skill("echo", "test skill", Mock(return_value="unchanged"))
        stack.enter_context(patch("messaging_llm_bot.bot.SkillManager.load", side_effect=lambda: {"echo": self.skill}))
        self.bot = BotRunner(BotConfig(
            telegram=TelegramConfig(bot_token="test"),
            llm=LLMConfig(base_url="https://example.invalid/v1", api_key="test", model="test"),
            runtime=RuntimeConfig(max_concurrent_requests=2),
        ))
        self.bot._build_messages = Mock(side_effect=lambda _key, text, **_kw: [{"role": "user", "content": text}])
        self.bot._typing_indicator = Mock(side_effect=lambda *_args: nullcontext())
        self.bot._send_message = Mock()
        self.bot._append_agenta_query_log = Mock()
        self.bot.llm.http_client = Mock(spec=["post_json"])

    @staticmethod
    def response(text, *, finish="stop"):
        return {"choices": [{"message": {"content": text}, "finish_reason": finish}], "usage": {"total_tokens": 10}}

    @classmethod
    def call(cls, name="echo", args=None, *, done=False):
        return cls.response(json.dumps({"skill_call": {"name": name, "args": args or {}, "done": done}}))

    def test_final_answer_at_chain_limit_is_returned(self) -> None:
        self.bot.config.runtime.max_skill_chain_steps = 1
        self.bot.llm.http_client.post_json.side_effect = [self.call(), self.response("Finished correctly")]
        self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertTrue(self.traces[-1]["final_reply"].startswith("Finished correctly"))
        self.assertNotIn("stop_reason", self.traces[-1]["metrics"])
        self.skill.run.assert_called_once()

    def test_chain_limit_does_not_execute_an_extra_skill(self) -> None:
        self.bot.config.runtime.max_skill_chain_steps = 1
        self.bot.llm.http_client.post_json.side_effect = [self.call(), self.call()]
        with self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        self.skill.run.assert_called_once()
        self.assertEqual(self.traces[-1]["metrics"]["stop_reason"], "max_skill_chain_steps")

    def test_repeated_result_stops_without_another_request(self) -> None:
        self.bot.config.runtime.max_repeated_skill_results = 3
        self.bot.llm.http_client.post_json.return_value = self.call()
        with self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertEqual(self.skill.run.call_count, 3)
        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 3)
        self.assertEqual(self.traces[-1]["metrics"]["stop_reason"], "repeated_skill_result")
        self.assertEqual(len(self.traces[-1]["steps"]), 3)
        self.assertEqual(self.bot._request_state.trace_steps, [])
        self.assertIsNone(self.bot._request_state.stop_reason)

    def test_changing_poll_results_are_progress(self) -> None:
        self.bot.config.runtime.max_repeated_skill_results = 2
        self.skill.run.side_effect = ["pending", "50%", "complete"]
        self.bot.llm.http_client.post_json.side_effect = [self.call(), self.call(), self.call(), self.response("Done")]
        self.assertTrue(self.bot._handle_message(1, "poll"))
        self.assertEqual(self.skill.run.call_count, 3)
        self.assertNotIn("stop_reason", self.traces[-1]["metrics"])

    def test_a_done_result_finishes_even_when_identical(self) -> None:
        self.bot.config.runtime.max_repeated_skill_results = 2
        self.bot.llm.http_client.post_json.side_effect = [self.call(), self.call(done=True)]
        self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertTrue(self.traces[-1]["final_reply"].startswith("unchanged"))
        self.assertNotIn("stop_reason", self.traces[-1]["metrics"])
        self.assertEqual(self.traces[-1]["steps"][-1]["context_chars"], 0)

    def test_mutating_plugin_arguments_cannot_corrupt_trace_or_loop_detection(self) -> None:
        self.bot.config.runtime.max_repeated_skill_results = 2

        def mutate(_workspace, args):
            args["nested"]["value"] = "changed"
            return "unchanged"

        self.skill.run.side_effect = mutate
        self.bot.llm.http_client.post_json.return_value = self.call(args={"nested": {"value": "original"}})
        with self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertEqual(self.traces[-1]["steps"][0]["skill_call"]["args"], {"nested": {"value": "original"}})
        self.assertEqual(self.skill.run.call_count, 2)

    def test_budget_covers_follow_up_and_preserves_completed_work(self) -> None:
        self.bot.config.runtime.max_llm_requests_per_turn = 1
        self.bot.llm.http_client.post_json.return_value = self.call()
        with self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._handle_message(1, "work"))
        trace = self.traces[-1]
        self.assertEqual(trace["error"], "request_budget_exceeded")
        self.assertEqual(trace["last_skill_result"], "unchanged")
        self.assertEqual(trace["metrics"]["request_count"], 1)
        self.assertEqual(trace["metrics"]["usage"], {"total_tokens": 10})
        self.assertEqual(trace["metrics"]["stop_reason"], "max_llm_requests_per_turn")
        self.bot.llm.http_client.post_json.assert_called_once()
        self.assertIn("Completed actions", trace["final_reply"])

        self.bot.llm.http_client.post_json.return_value = self.response("Fresh turn")
        self.assertTrue(self.bot._handle_message(1, "another request"))
        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 1)
        self.assertNotIn("stop_reason", self.traces[-1]["metrics"])

    def test_concurrent_conversations_have_independent_budgets_and_results(self) -> None:
        self.bot.config.runtime.max_llm_requests_per_turn = 2
        self.bot.config.runtime.max_skill_result_chars = 512
        barrier = threading.Barrier(2, timeout=3)
        outcomes = []

        def respond(_url, payload, **_kwargs):
            barrier.wait()
            if len(payload["messages"]) == 1:
                return self.call(args={"text": payload["messages"][0]["content"]})
            return self.response("done")

        self.skill.run.side_effect = lambda _workspace, args: args["text"] * 1000
        self.bot.llm.http_client.post_json.side_effect = respond
        threads = [threading.Thread(target=lambda value=value: outcomes.append(self.bot._handle_message(value, str(value))))
                   for value in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, [True, True])
        for trace in self.traces:
            self.assertEqual(trace["last_skill_result"], trace["conversation_id"] * 1000)
            self.assertEqual(trace["metrics"]["request_count"], 2)

    def test_large_result_is_bounded_before_first_follow_up_and_recoverable(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 512
        result = "start" + "x" * 600 + "MIDDLE EVIDENCE" + "y" * 600 + "end"
        self.skill.run.return_value = result
        prompts = []
        responses = iter([self.call(done=True), self.call("read_skill_result", {"step": 1, "offset": 605, "limit": 15}), self.response("Done")])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            return next(responses)

        self.bot.llm.http_client.post_json.side_effect = respond
        self.assertTrue(self.bot._handle_message(1, "read"))
        self.assertNotIn("MIDDLE EVIDENCE", prompts[1][-1]["content"])
        self.assertIn("MIDDLE EVIDENCE", prompts[2][-1]["content"])
        self.assertIn("If you need another skill call", prompts[1][-1]["content"])
        step = self.traces[-1]["steps"][0]
        self.assertEqual(step["skill_result"], result)
        self.assertEqual(step["context_chars"], 512)
        self.assertTrue(step["result_truncated"])
        self.assertEqual(self.skill.run.call_count, 1)

    def test_reader_cannot_access_previous_turn(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 512
        self.bot.llm.http_client.post_json.side_effect = [self.call(done=True), self.call("read_skill_result", {"step": 1}), self.response("Unavailable")]
        self.assertTrue(self.bot._handle_message(1, "first"))
        self.assertTrue(self.bot._handle_message(1, "second"))
        self.assertIn("step is unavailable in this turn", self.traces[-1]["steps"][0]["skill_result"])

    def test_reader_is_only_exposed_when_limits_are_enabled(self) -> None:
        self.assertNotIn("read_skill_result", self.bot._load_runtime_skills())
        self.bot.config.runtime.max_skill_result_chars = 512
        self.assertIn("read_skill_result", self.bot._load_runtime_skills())

    def test_malformed_skill_result_is_reported_to_the_model(self) -> None:
        self.skill.run.return_value = {"unexpected": "object"}
        self.bot.llm.http_client.post_json.side_effect = [self.call(), self.response("The skill failed")]
        with self.assertLogs(level="ERROR"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertIn("Expected a text result, got dict", self.traces[-1]["last_skill_result"])
        self.assertTrue(self.traces[-1]["final_reply"].startswith("The skill failed"))

    def test_empty_skill_result_can_be_handled_by_a_follow_up(self) -> None:
        self.skill.run.return_value = ""
        self.bot.llm.http_client.post_json.side_effect = [self.call(), self.response("There were no results")]
        self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertEqual(self.traces[-1]["last_skill_result"], "")
        self.assertTrue(self.traces[-1]["final_reply"].startswith("There were no results"))

    def test_large_web_results_stay_bounded_without_changing_recovery_offsets(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 512
        web_skill = Skill("web_search", "test search", Mock(return_value="DuckDuckGo results for: test\nHTML:\n" + "x" * 2000))
        calls = []
        replies = iter([self.call("web_search"), self.call(), self.response("Done")])

        def respond(_url, payload, **_kwargs):
            calls.append(json.loads(json.dumps(payload["messages"])))
            return next(replies)

        with patch("messaging_llm_bot.bot.SkillManager.load", side_effect=lambda: {"echo": self.skill, "web_search": web_skill}):
            self.bot.llm.http_client.post_json.side_effect = respond
            self.assertTrue(self.bot._handle_message(1, "search"))
        self.assertIn("step 1 truncated", calls[1][-1]["content"])
        self.assertEqual(calls[1][-1]["content"], calls[2][-3]["content"])
        self.assertEqual(self.traces[-1]["steps"][0]["skill_result"], web_skill.run.return_value)

    def test_done_follow_up_cannot_leak_tool_json_as_the_answer(self) -> None:
        self.skill.requires_llm_response = True
        self.bot.llm.http_client.post_json.side_effect = [self.call(done=True), self.call(done=True), self.response("Finished")]
        self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertTrue(self.traces[-1]["final_reply"].startswith("Finished"))
        self.assertEqual(self.skill.run.call_count, 2)

    def test_scheduled_runs_also_obey_the_request_budget(self) -> None:
        self.bot.config.runtime.max_llm_requests_per_turn = 1
        self.bot._resolve_hourly_target = Mock(return_value=None)
        self.bot.llm.http_client.post_json.return_value = self.response("partial", finish="length")
        with self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._run_hourly_task("work", datetime.now(timezone.utc)))
        self.bot.llm.http_client.post_json.assert_called_once()
        self.bot._send_message.assert_not_called()
