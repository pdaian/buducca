import json
import threading
import unittest
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from assistant_framework.traces import build_trace_metrics
from messaging_llm_bot.bot import BotRunner
from messaging_llm_bot.config import BotConfig, LLMConfig, RuntimeConfig, TelegramConfig
from messaging_llm_bot.http import RequestTimeoutError


class TraceMetricsTests(unittest.TestCase):
    def test_totals_preserve_missing_usage_and_report_coverage(self) -> None:
        requests = [
            {"duration_ms": 12.5, "continuation": False, "usage": {"prompt_tokens": 10, "cached_tokens": 0}},
            {"duration_ms": 20.0, "continuation": True, "usage": {}},
            {"duration_ms": 7.5, "continuation": False, "usage": {"prompt_tokens": 5, "reasoning_tokens": 3}},
        ]

        metrics = build_trace_metrics(requests, skill_chain_length=2)

        self.assertEqual(metrics["request_count"], 3)
        self.assertEqual(metrics["continuation_count"], 1)
        self.assertEqual(metrics["skill_chain_length"], 2)
        self.assertEqual(metrics["duration_ms"], 40.0)
        self.assertEqual(metrics["usage"], {"prompt_tokens": 15, "cached_tokens": 0, "reasoning_tokens": 3})
        self.assertEqual(metrics["usage_request_counts"], {"prompt_tokens": 2, "cached_tokens": 1, "reasoning_tokens": 1})
        self.assertNotIn("total_tokens", metrics["usage"])
        self.assertEqual(metrics["requests"], requests)

    def test_no_requests_does_not_invent_token_counts(self) -> None:
        metrics = build_trace_metrics([], skill_chain_length=0)

        self.assertEqual(metrics["request_count"], 0)
        self.assertEqual(metrics["duration_ms"], 0)
        self.assertEqual(metrics["usage"], {})
        self.assertEqual(metrics["usage_request_counts"], {})


class BotTraceMetricsTests(unittest.TestCase):
    """Exercise message handling with HTTP, delivery, and filesystem writes mocked."""

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
        self.skill = SimpleNamespace(
            requires_llm_response=False,
            build_action=None,
            run=Mock(return_value="skill result"),
        )
        stack.enter_context(patch.object(BotRunner, "_load_runtime_skills", return_value={"echo": self.skill}))
        self.bot = BotRunner(BotConfig(
            telegram=TelegramConfig(bot_token="test"),
            llm=LLMConfig(base_url="https://example.invalid/v1", api_key="test", model="test-model"),
            runtime=RuntimeConfig(max_concurrent_requests=2),
        ))
        self.bot._build_messages = Mock(side_effect=lambda _key, text, **_kwargs: [{"role": "user", "content": text}])
        self.bot._typing_indicator = Mock(side_effect=lambda *_args: nullcontext())
        self.bot._send_message = Mock()
        self.bot._append_agenta_query_log = Mock()
        self.bot.llm.http_client = Mock(spec=["post_json"])

    @staticmethod
    def response(text, usage=None):
        return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": usage}

    def test_trace_accumulates_all_model_calls_and_resets_for_next_message(self) -> None:
        self.bot.llm.http_client.post_json.side_effect = [
            self.response('{"skill_call": {"name": "echo", "args": {}, "done": false}}',
                          {"prompt_tokens": 10, "completion_tokens": 3}),
            self.response("finished", {"prompt_tokens": 20, "completion_tokens": 5}),
            self.response("another answer", {"prompt_tokens": 7}),
        ]

        self.assertTrue(self.bot._handle_message(1, "run echo"))
        trace = self.traces[-1]
        self.assertEqual(trace["metrics"]["request_count"], 2)
        self.assertEqual(trace["metrics"]["skill_chain_length"], 1)
        self.assertEqual(trace["metrics"]["usage"], {"prompt_tokens": 30, "completion_tokens": 8})
        self.assertEqual(trace["metrics"]["usage_request_counts"], {"prompt_tokens": 2, "completion_tokens": 2})
        self.assertEqual(trace["last_skill_result"], "skill result")
        self.assertTrue(trace["final_reply"].startswith("finished\n\n"))
        self.skill.run.assert_called_once()
        self.assertEqual(self.bot._request_state.llm_requests, [])

        self.assertTrue(self.bot._handle_message(1, "next"))
        self.assertEqual(self.traces[-1]["metrics"]["usage"], {"prompt_tokens": 7})
        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 1)
        self.assertEqual(self.traces[-1]["metrics"]["skill_chain_length"], 0)
        self.assertNotIn("last_action", self.traces[-1])

    def test_failed_follow_up_keeps_completed_skill_and_partial_usage(self) -> None:
        for error in (RequestTimeoutError("timeout"), RuntimeError("endpoint failed"), ValueError("invalid response")):
            with self.subTest(error=type(error).__name__):
                self.bot.llm.http_client.post_json.side_effect = [
                    self.response('{"skill_call": {"name": "echo", "args": {}, "done": false}}',
                                  {"prompt_tokens": 10}),
                    error,
                ]

                with self.assertLogs(level="WARNING"):
                    self.assertFalse(self.bot._handle_message(1, "run echo"))

                trace = self.traces[-1]
                self.assertEqual(trace["error"], "timeout" if isinstance(error, RequestTimeoutError) else "internal_error")
                self.assertEqual(trace["last_action"]["name"], "echo")
                self.assertEqual(trace["last_skill_result"], "skill result")
                self.assertEqual(trace["metrics"]["skill_chain_length"], 1)
                self.assertEqual(trace["metrics"]["request_count"], 2)
                self.assertEqual(trace["metrics"]["usage"], {"prompt_tokens": 10})
                self.assertEqual(trace["metrics"]["usage_request_counts"], {"prompt_tokens": 1})
                self.assertEqual(trace["metrics"]["requests"][-1]["error"], type(error).__name__)
                self.assertEqual(self.bot._request_state.llm_requests, [])

    def test_operator_command_does_not_reuse_previous_request_metrics(self) -> None:
        self.bot.llm.http_client.post_json.return_value = self.response("hello", {"total_tokens": 10})
        self.assertTrue(self.bot._handle_message(1, "hello"))
        self.bot._build_status_message = Mock(return_value="status")

        self.assertTrue(self.bot._handle_message(1, "/status"))

        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 0)
        self.assertEqual(self.traces[-1]["metrics"]["usage"], {})
        self.bot.llm.http_client.post_json.assert_called_once()

    def test_concurrent_conversations_keep_separate_usage(self) -> None:
        barrier = threading.Barrier(2, timeout=2)
        outcomes = []

        def respond(_url, payload, **_kwargs):
            barrier.wait()
            return self.response("hello", {"total_tokens": int(payload["messages"][0]["content"])})

        self.bot.llm.http_client.post_json.side_effect = respond
        threads = [
            threading.Thread(target=lambda value=tokens: outcomes.append(self.bot._handle_message(value, str(value))))
            for tokens in (7, 23)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

        self.assertEqual(outcomes, [True, True])
        self.assertEqual(len(self.traces), 2)
        for trace in self.traces:
            self.assertEqual(trace["metrics"]["request_count"], 1)
            self.assertEqual(trace["metrics"]["usage"], {"total_tokens": int(trace["conversation_id"])})

    def test_invalid_runner_has_no_endpoint_requests(self) -> None:
        with self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._handle_message(1, "/9 hello"))

        self.assertEqual(self.traces[-1]["error"], "invalid_runner")
        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 0)
        self.bot.llm.http_client.post_json.assert_not_called()

    def test_custom_client_without_metrics_remains_compatible(self) -> None:
        self.bot.llm = SimpleNamespace(generate_reply=lambda *_args, **_kwargs: "hello")

        self.assertTrue(self.bot._handle_message(1, "hello"))

        self.assertEqual(self.traces[-1]["final_reply"], "hello")
        self.assertEqual(self.traces[-1]["metrics"]["requests"], [])


if __name__ == "__main__":
    unittest.main()
