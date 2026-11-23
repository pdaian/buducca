import io
import json
import threading
import unittest
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from assistant_framework.skills import Skill
from assistant_framework.workspace import Workspace
from messaging_llm_bot.bot import BotRunner
from messaging_llm_bot.config import BotConfig, LLMConfig, RuntimeConfig, TelegramConfig
from messaging_llm_bot.http import ContextLengthExceededError, HttpClient, RequestTimeoutError
from skills import file as file_skill


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

    @contextmanager
    def file_workspace(self, contents):
        workspace = Mock(spec=Workspace)
        workspace.resolve.side_effect = lambda path: Mock(exists=lambda: path in contents)
        workspace.read_text.side_effect = lambda path, default="": contents.get(path, default)
        skill = Skill(**file_skill.register())
        with patch("messaging_llm_bot.bot.SkillManager.load", side_effect=lambda: {"file": skill}), \
                patch.object(self.bot, "_workspace", workspace):
            self.bot._refresh_skills()
            yield workspace

    @contextmanager
    def conversation_history(self, turns=4, *, conversation_key=1):
        history = [
            {"role": role, "content": f"Earlier {role} {turn}"}
            for turn in range(turns) for role in ("user", "assistant")
        ]
        self.bot._history[conversation_key].extend(history)
        with patch.object(self.bot, "_build_messages", wraps=BotRunner._build_messages.__get__(self.bot)), \
                patch.object(self.bot, "_build_system_prompt", return_value="Keep all system instructions."), \
                patch("messaging_llm_bot.bot.search_workspace", return_value=[]), \
                patch("messaging_llm_bot.bot.build_structured_memory_context", return_value="Keep workspace context."):
            yield history

    def test_file_prompt_exposes_configured_cap_without_inventing_token_capacity(self) -> None:
        with self.file_workspace({}), patch("messaging_llm_bot.bot.CollectorManager.load_manifests", return_value=[]):
            for cap in (512, 4000, None):
                with self.subTest(cap=cap):
                    self.bot.config.runtime.max_skill_result_chars = cap
                    prompt = self.bot._build_system_prompt()
                    self.assertIn("remaining token capacity are not known", prompt)
                    if cap is None:
                        self.assertIn("no runtime character cap", prompt)
                        self.assertNotIn("read_skill_result", prompt)
                    else:
                        self.assertIn(f"cap of {cap} characters per call", prompt)
                        self.assertIn("read_skill_result", prompt)

    def test_sequential_file_reads_report_remaining_calls_and_stop_at_chain_limit(self) -> None:
        self.bot.config.runtime.max_skill_chain_steps = 3
        prompts = []
        replies = iter([
            self.call("file", {"path": "first.txt", "read_mode": "head", "read_line_limit": 2}),
            self.call("file", {"command": " READ ", "path": "first.txt", "start_line": 3, "end_line": 4}),
            self.call("file", {"action": "read", "path": "second.txt"}),
            self.response("Both files inspected"),
        ])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            return next(replies)

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.file_workspace({"first.txt": "one\ntwo\nthree\nfour", "second.txt": "other evidence"}):
            self.assertTrue(self.bot._handle_message(1, "Read both files"))
        steps = self.traces[-1]["steps"]
        self.assertEqual([step["skill_result"] for step in steps], [
            "first.txt:\none\ntwo", "first.txt:\nthree\nfour", "second.txt:\nother evidence",
        ])
        for index, remaining in enumerate((2, 1, 0), start=1):
            self.assertIn(f"Remaining skill-chain calls: {remaining}", prompts[index][-1]["content"])
        self.assertIn("No runtime read context cap", prompts[1][-1]["content"])
        self.assertIn("start_line one past the last fully inspected line", prompts[2][-1]["content"])
        self.assertNotIn("If you need another skill call", prompts[3][-1]["content"])
        self.assertNotIn("stop_reason", self.traces[-1]["metrics"])

    def test_truncated_file_result_is_recovered_across_calls_without_rereading_files(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 512
        original = "first.txt:\n" + "é🙂" * 300 + "\n\nsecond.txt:\nother evidence"
        recovered = []
        prompts = []

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            steps = self.bot._get_trace_steps()
            if not steps:
                return self.call("file", {"paths": ["first.txt", "second.txt"]})
            if len(steps) == 1:
                return self.call("read_skill_result", {"step": 1, "offset": 0, "limit": 10000})
            page = steps[-1]["skill_result"]
            header, _, body = page.partition("\n")
            recovered.append(body)
            offset = int(header.rsplit("next offset ", 1)[1].rstrip("]"))
            if offset == len(original):
                return self.response("Both files recovered")
            return self.call("read_skill_result", {"step": 1, "offset": offset, "limit": 10000})

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.file_workspace({"first.txt": "é🙂" * 300, "second.txt": "other evidence"}) as workspace:
            self.assertTrue(self.bot._handle_message(1, "Read both files completely"))
            self.assertEqual(workspace.read_text.call_count, 2)
        steps = self.traces[-1]["steps"]
        self.assertEqual(steps[0]["skill_result"], original)
        self.assertEqual("".join(recovered), original)
        self.assertTrue(all(step["context_chars"] <= 512 for step in steps))
        self.assertTrue(all(step["skill_call"]["args"]["step"] == 1 for step in steps[1:]))
        self.assertIn("same original step", prompts[-1][-1]["content"])
        self.assertIn("next offset", prompts[-1][-1]["content"])

    def test_file_advice_updates_after_context_recovery_even_for_a_small_latest_read(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        prompts = []
        replies = iter([
            self.call("file", {"path": "large.txt"}),
            self.call("file", {"path": "small.txt"}),
            ContextLengthExceededError("context full"),
            self.call("read_skill_result", {"step": 1, "offset": 3000, "limit": 10000}),
            self.response("Done"),
        ])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.file_workspace({"large.txt": "x" * 6000, "small.txt": "small"}) as workspace, self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "Read the files"))
            self.assertEqual(workspace.read_text.call_count, 2)
        self.assertIn("Current read context cap: 4000", prompts[2][-1]["content"])
        for message in (prompts[3][2], prompts[3][4], prompts[4][-1]):
            self.assertIn("Current read context cap: 2000", message["content"])
        steps = self.traces[-1]["steps"]
        self.assertEqual(steps[0]["context_chars"], 2000)
        self.assertFalse(steps[1]["result_truncated"])
        self.assertLessEqual(steps[2]["result_chars"], 2000)

    def test_disabled_cap_and_file_mutations_do_not_suggest_unavailable_recovery(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = None
        with self.file_workspace({}):
            read_prompt = self.bot._continue_skill_chain_prompt(
                "file", "content", allow_follow_up_skill=True, skill_args={"action": "read"},
            )
            self.assertIn("No runtime read context cap", read_prompt)
            self.assertNotIn("read_skill_result", read_prompt)
            for args in ({"action": "write"}, {"command": "append"}, {"action": "list", "command": "read"}):
                prompt = self.bot._continue_skill_chain_prompt(
                    "file", "complete", allow_follow_up_skill=True, skill_args=args,
                )
                self.assertNotIn("read context cap", prompt)
                self.assertNotIn("read_skill_result", prompt)

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

    def test_default_returns_full_large_result_on_first_follow_up(self) -> None:
        result = "start" + "é🙂" * 8000 + "MIDDLE EVIDENCE" + "y" * 16000 + "end"
        for done in (False, True):
            with self.subTest(done=done):
                self.skill.run.reset_mock()
                self.skill.run.return_value = result
                self.bot.llm.http_client.post_json.reset_mock()
                prompts = []
                responses = iter([self.call(done=done), self.response("Done")])

                def respond(_url, payload, **_kwargs):
                    prompts.append(json.loads(json.dumps(payload["messages"])))
                    return next(responses)

                self.bot.llm.http_client.post_json.side_effect = respond
                self.assertTrue(self.bot._handle_message(1, "read"))
                self.assertEqual(len(prompts), 2)
                self.assertIn(result, prompts[1][-1]["content"])
                self.assertNotIn("read_skill_result", prompts[1][-1]["content"])
                self.assertNotIn("read_skill_result", self.bot._skills)
                steps = self.traces[-1]["steps"]
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0]["result_chars"], len(result))
                self.assertEqual(steps[0]["context_chars"], len(result))
                self.assertEqual(steps[0]["skill_result"], result)
                self.assertFalse(steps[0]["result_truncated"])
                self.assertTrue(self.traces[-1]["final_reply"].startswith("Done"))
                self.skill.run.assert_called_once()

    def test_default_keeps_full_file_results_in_context_across_calls(self) -> None:
        first = "start\n" + "é🙂" * 3000 + "\nMIDDLE EVIDENCE\n" + "y" * 3000 + "\nend"
        prompts = []
        responses = iter([
            self.call("file", {"path": "first.txt"}),
            self.call("file", {"path": "second.txt"}),
            self.response("Both files inspected"),
        ])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            return next(responses)

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.file_workspace({"first.txt": first, "second.txt": "other evidence"}) as workspace:
            self.assertTrue(self.bot._handle_message(1, "Read both files completely"))
            self.assertEqual(workspace.read_text.call_count, 2)
        self.assertEqual(len(prompts), 3)
        for prompt in prompts[1:]:
            self.assertIn("first.txt:\n" + first, prompt[2]["content"])
            self.assertNotIn("read_skill_result", str(prompt))
        self.assertIn("second.txt:\nother evidence", prompts[-1][-1]["content"])
        steps = self.traces[-1]["steps"]
        self.assertEqual(len(steps), 2)
        for step in steps:
            self.assertEqual(step["context_chars"], step["result_chars"])
            self.assertFalse(step["result_truncated"])

    def test_default_recovery_retains_readback_and_only_caps_the_current_turn(self) -> None:
        result = "start" + "é🙂" * 1500 + "MIDDLE EVIDENCE" + "y" * 3000 + "end"
        self.skill.run.return_value = result
        prompts = []
        replies = iter([
            self.call(), self.call(), ContextLengthExceededError("context full"),
            self.response(json.dumps({"read_skill_result": {"step": 1, "offset": 3005, "limit": 15}})),
            self.call("read_skill_result", {"step": 1, "limit": 10000}, done=True), self.call(), self.response("Done"),
            self.call(), self.response("Next turn"),
        ])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            # Another conversation may refresh the shared registry at any time.
            self.bot._refresh_skills()
            self.assertNotIn("read_skill_result", self.bot._skills)
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "read"))
        self.assertEqual(self.skill.run.call_count, 3)
        self.assertIn(result, prompts[1][-1]["content"])
        self.assertIn(result, prompts[2][-1]["content"])
        self.assertNotIn("MIDDLE EVIDENCE", str(prompts[3]))
        self.assertIn("MIDDLE EVIDENCE", prompts[4][-1]["content"])
        steps = self.traces[-1]["steps"]
        self.assertEqual([step["context_chars"] for step in steps[:2]], [2000, 2000])
        self.assertEqual(steps[2]["skill_result"].split("\n", 1)[1], "MIDDLE EVIDENCE")
        self.assertIn("next offset 1840", steps[3]["skill_result"])
        self.assertLessEqual(steps[3]["result_chars"], 2000)
        self.assertEqual(steps[4]["context_chars"], 2000)
        for index in (0, 1, 4):
            self.assertEqual(steps[index]["skill_result"], result)
        self.assertIsNone(self.bot._request_state.skill_result_chars)
        self.assertIsNone(self.bot.config.runtime.max_skill_result_chars)
        self.assertIsNone(self.bot._get_skill("read_skill_result"))
        self.assertTrue(self.bot._handle_message(1, "new work"))
        self.assertEqual(self.skill.run.call_count, 4)
        self.assertIn(result, prompts[-1][-1]["content"])
        self.assertFalse(self.traces[-1]["steps"][0]["result_truncated"])

    def test_context_errors_on_second_request_recover_without_repeating_skill(self) -> None:
        for streaming, status, cap in (
            (False, 400, 4000), (True, 400, 4000), (False, 500, 4000), (True, 500, 4000), (True, 200, 4000),
            (False, 400, None), (True, 400, None),
            (False, 500, None), (True, 500, None), (False, 200, None), (True, 200, None),
        ):
            with self.subTest(streaming=streaming, status=status, cap=cap):
                self.bot.config.runtime.max_skill_result_chars = cap
                self.skill.run.reset_mock()
                result = "start" + "x" * 15602 + "end" if cap or status == 400 else "short skill result"
                self.skill.run.return_value = result
                self.bot.llm.http_client = HttpClient(1)
                self.bot.llm._runners[0].extra_body = {"stream": streaming}
                messages = [{"role": "system", "content": "Keep these instructions."},
                            {"role": "user", "content": "Keep the full request."}]
                self.bot._build_messages = Mock(side_effect=lambda *_args, **_kwargs: [dict(m) for m in messages])
                responses = []
                for response in (self.call(), self.response("Recovered")):
                    body = io.BytesIO(json.dumps(response).encode())
                    body.headers = {"Content-Type": "application/json"}
                    responses.append(body)
                if status == 400:
                    error_body = {"error": {"type": "exceed_context_size_error", "n_prompt_tokens": 15185, "n_ctx": 8192}}
                else:
                    error_body = {"error": {"code": 500, "type": "server_error", "message": "Context size has been exceeded."}}
                raw = json.dumps(error_body).encode()
                if status == 200:
                    error = io.BytesIO(b"data: " + raw + b"\n\ndata: [DONE]\n\n" if streaming else raw)
                    error.headers = {"Content-Type": "text/event-stream" if streaming else "application/json"}
                else:
                    error = HTTPError("https://example.invalid/v1/chat/completions", status, "Error", {}, io.BytesIO(raw))
                with patch("messaging_llm_bot.http.urlopen", side_effect=[responses[0], error, responses[1]]) as request, self.assertLogs(level="WARNING"):
                    self.assertTrue(self.bot._handle_message(1, "work"))
                self.skill.run.assert_called_once()
                payloads = [json.loads(call.args[0].data) for call in request.call_args_list]
                self.assertEqual(len(payloads), 3)
                self.assertEqual(payloads[2]["messages"][:2], messages)
                self.assertEqual(payloads[2]["messages"][2], payloads[1]["messages"][2])
                if status == 400:
                    self.assertLess(len(payloads[2]["messages"][-1]["content"]), len(payloads[1]["messages"][-1]["content"]))
                    self.assertNotIn("cache_prompt", payloads[2])
                else:
                    self.assertEqual(payloads[2], {**payloads[1], "cache_prompt": False})
                trace = self.traces[-1]
                self.assertTrue(trace["final_reply"].startswith("Recovered"))
                self.assertEqual(trace["steps"][0]["skill_result"], result)
                self.assertEqual(trace["steps"][0]["context_chars"], 2000 if status == 400 else cap or len(result))
                self.assertEqual(trace["metrics"]["request_count"], 3)
                self.assertEqual(trace["metrics"]["requests"][1]["error"], "ContextLengthExceededError")
                self.assertEqual(trace["metrics"]["requests"][1]["context_error_kind"], "length" if status == 400 else "capacity")
                self.assertEqual(trace["metrics"]["requests"][2]["cache_prompt_retry"], status != 400)
                self.assertEqual(self.bot.config.runtime.max_skill_result_chars, cap)
                self.assertTrue(all(response.closed for response in [*responses, error]))

    def test_stream_fallback_recovers_follow_up_without_repeating_completed_skills(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        for context_error in (False, True):
            with self.subTest(context_error=context_error):
                self.skill.run.reset_mock()
                self.skill.run.return_value = "x" * 6000
                self.bot.llm.http_client = HttpClient(30)
                responses = []
                for data in (self.call(), self.response("Recovered")):
                    response = io.BytesIO(json.dumps(data).encode())
                    response.headers = {"Content-Type": "application/json"}
                    responses.append(response)
                # Even a complete-looking skill call from a failed stream must be discarded.
                events = [
                    {"choices": [{"delta": {"reasoning_content": "private reasoning"}}]},
                    {"choices": [{"delta": self.call()["choices"][0]["message"]}], "usage": {"completion_tokens": 400}},
                    {"error": {"message": "PRIVATE server error"}},
                ]
                failed_stream = io.BytesIO(b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events))
                failed_stream.headers = {"Content-Type": "text/event-stream"}
                responses.insert(1, failed_stream)
                if context_error:
                    overflow = io.BytesIO(b'{"error":{"message":"Context size has been exceeded."}}')
                    overflow.headers = {"Content-Type": "application/json"}
                    responses.insert(2, overflow)
                self.bot.config.runtime.max_llm_requests_per_turn = len(responses)
                with patch("messaging_llm_bot.http.urlopen", side_effect=responses) as request, self.assertLogs(level="WARNING"):
                    self.assertTrue(self.bot._handle_message(1, "work"))
                self.skill.run.assert_called_once()
                payloads = [json.loads(call.args[0].data) for call in request.call_args_list]
                self.assertEqual(payloads[2]["messages"], payloads[1]["messages"])
                self.assertFalse(payloads[2]["stream"])
                trace = self.traces[-1]
                self.assertTrue(trace["final_reply"].startswith("Recovered"))
                self.assertEqual(len(trace["steps"]), 1)
                self.assertEqual(trace["steps"][0]["context_chars"], 4000)
                self.assertEqual(trace["metrics"]["request_count"], len(responses))
                metrics = trace["metrics"]["requests"]
                self.assertEqual(metrics[1]["error"], "StreamError")
                self.assertEqual(metrics[1]["usage"], {"completion_tokens": 400})
                if context_error:
                    self.assertEqual(metrics[2]["error"], "ContextLengthExceededError")
                    self.assertEqual(payloads[3], {**payloads[2], "cache_prompt": False})
                self.assertTrue(all(response.closed for response in responses))

    def test_recovery_compacts_previous_results_and_retains_readback(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        result = "start" + "x" * 3000 + "MIDDLE EVIDENCE" + "y" * 3000 + "end"
        self.skill.run.return_value = result
        prompts = []
        responses = iter([
            self.call(), self.call(), ContextLengthExceededError("context full"),
            self.call("read_skill_result", {"step": 1, "offset": 3005, "limit": 15}), self.response("Done"),
        ])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "read"))
        self.assertEqual(self.skill.run.call_count, 2)
        self.assertNotIn("MIDDLE EVIDENCE", prompts[3][-1]["content"])
        self.assertIn("MIDDLE EVIDENCE", prompts[4][-1]["content"])
        self.assertLess(len(prompts[3][2]["content"]), len(prompts[2][2]["content"]))
        self.assertEqual([step["context_chars"] for step in self.traces[-1]["steps"][:2]], [2000, 2000])
        self.assertEqual(self.traces[-1]["steps"][0]["skill_result"], result)

    def test_context_recovery_is_bounded_and_reports_actionable_error(self) -> None:
        self.skill.run.return_value = "x" * 5000
        self.bot.llm.http_client.post_json.side_effect = [self.call(), *[ContextLengthExceededError("context full")] * 4]
        with self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._handle_message(1, "work"))
        self.skill.run.assert_called_once()
        trace = self.traces[-1]
        self.assertEqual(trace["metrics"]["request_count"], 5)
        self.assertEqual(trace["error"], "context_length_exceeded")
        self.assertEqual(trace["metrics"]["stop_reason"], "context_length_exceeded")
        self.assertEqual(trace["steps"][0]["context_chars"], 512)
        self.assertIn("loaded per-request context", trace["final_reply"])
        self.assertIn("output-token limit", trace["final_reply"])
        self.assertIn("reduce concurrent requests", trace["final_reply"])
        self.assertIn("Completed actions", trace["final_reply"])

    def test_context_recovery_reaches_minimum_cap_without_repeating_skill(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        result = "x" * 6000
        self.skill.run.return_value = result
        caps = []

        def respond(_url, payload, **_kwargs):
            steps = self.bot._get_trace_steps()
            if not steps:
                return self.call()
            caps.append(steps[0]["context_chars"])
            if steps[0]["context_chars"] > 512:
                raise ContextLengthExceededError("context full")
            return self.response("Recovered")

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertEqual(caps, [4000, 2000, 1000, 512])
        self.skill.run.assert_called_once()
        self.assertEqual(self.traces[-1]["steps"][0]["skill_result"], result)
        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 5)

    def test_chain_keeps_main_prompt_only_in_original_and_latest_follow_up(self) -> None:
        main_prompt = "[Main prompt]\nKeep the complete hourly instructions."
        messages = [{"role": "system", "content": "Keep the system instructions."},
                    {"role": "user", "content": main_prompt}]
        self.bot._build_messages = Mock(side_effect=lambda *_args, **_kwargs: [dict(m) for m in messages])
        prompts = []
        replies = iter([self.call(args={"step": 1}), self.call(args={"step": 2}), self.response("Done")])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            return next(replies)

        self.bot.llm.http_client.post_json.side_effect = respond
        self.assertTrue(self.bot._handle_message(1, "work"))
        for prompt in prompts[1:]:
            self.assertEqual(prompt[:2], messages)
            self.assertTrue(prompt[-1]["content"].startswith(main_prompt))
            self.assertEqual(sum(m["content"].count(main_prompt) for m in prompt), 2)
        self.assertIn("Skill `echo` returned:\nunchanged", prompts[-1][3]["content"])
        self.assertEqual(self.skill.run.call_count, 2)

    def test_recovery_shrinks_results_already_below_half_the_configured_cap(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        for size in (600, 1500, 2000):
            with self.subTest(size=size):
                result = "x" * size
                self.skill.run.return_value = result
                self.skill.run.reset_mock()
                self.bot.llm.http_client.post_json.side_effect = [
                    self.call(), ContextLengthExceededError("context full"), self.response("Recovered"),
                ]
                with self.assertLogs(level="WARNING"):
                    self.assertTrue(self.bot._handle_message(1, "work"))
                self.skill.run.assert_called_once()
                trace = self.traces[-1]
                self.assertEqual(trace["metrics"]["request_count"], 3)
                self.assertLess(trace["steps"][0]["context_chars"], size)
                self.assertEqual(trace["steps"][0]["skill_result"], result)
                self.assertTrue(trace["steps"][0]["result_truncated"])

    def test_recovery_removes_duplicate_main_prompt_even_with_small_results(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 512
        main_prompt = ("[Main prompt]\n" + "Keep all these instructions. " * 100).strip()
        messages = [{"role": "system", "content": "Keep system instructions."},
                    {"role": "user", "content": "Earlier conversation"},
                    {"role": "assistant", "content": "Earlier answer"},
                    {"role": "user", "content": main_prompt}]
        self.bot._build_messages = Mock(side_effect=lambda *_args, **_kwargs: [dict(m) for m in messages])
        prompts = []
        replies = iter([self.call(args={"step": 1}), ContextLengthExceededError("context full"),
                        self.call(args={"step": 2}), self.response("Done")])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertIn(main_prompt, prompts[1][-1]["content"])
        for prompt in prompts[2:]:
            self.assertEqual(prompt[:4], messages)
            self.assertEqual(sum(m["content"].count(main_prompt) for m in prompt), 1)
            self.assertEqual(prompt[4], prompts[1][4])
            self.assertIn("Skill `echo` returned:\nunchanged", prompt[-1]["content"])
        self.assertEqual(self.skill.run.call_count, 2)
        self.assertFalse(any(step["result_truncated"] for step in self.traces[-1]["steps"]))

    def test_reduced_cap_applies_to_readback_and_later_results_only_in_current_turn(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        result = "x" * 6000
        self.skill.run.return_value = result
        self.bot.llm.http_client.post_json.side_effect = [
            self.call(), ContextLengthExceededError("context full"),
            self.call("read_skill_result", {"step": 1, "limit": 10000}), self.call(), self.response("Done"),
            self.call(), self.response("Next turn"),
        ]
        with self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        steps = self.traces[-1]["steps"]
        self.assertEqual(steps[0]["context_chars"], 2000)
        self.assertLessEqual(steps[1]["result_chars"], 2000)
        self.assertFalse(steps[1]["result_truncated"])
        self.assertEqual(steps[2]["context_chars"], 2000)
        self.assertIn("next offset 1840", steps[1]["skill_result"])
        self.assertIsNone(self.bot._request_state.skill_result_chars)
        self.assertTrue(self.bot._handle_message(1, "new work"))
        self.assertEqual(self.traces[-1]["steps"][0]["context_chars"], 4000)

    def test_context_recovery_consumes_existing_request_budget(self) -> None:
        self.bot.config.runtime.max_llm_requests_per_turn = 2
        self.skill.run.return_value = "x" * 5000
        self.bot.llm.http_client.post_json.side_effect = [self.call(), ContextLengthExceededError("context full")]
        with self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._handle_message(1, "work"))
        self.skill.run.assert_called_once()
        self.assertEqual(self.traces[-1]["error"], "request_budget_exceeded")
        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 2)

    def test_context_recovery_does_not_change_concurrent_conversation_limits(self) -> None:
        self.skill.run.return_value = "x" * 6000
        barrier = threading.Barrier(2, timeout=3)
        recovery_barrier = threading.Barrier(2, timeout=3)
        attempts = {"1": 0, "2": 0}
        outcomes = []

        def respond(_url, payload, **_kwargs):
            conversation = payload["messages"][0]["content"]
            attempts[conversation] += 1
            if attempts[conversation] == 1:
                barrier.wait()
                return self.call()
            if conversation == "1" and attempts[conversation] == 2:
                raise ContextLengthExceededError("context full")
            if conversation == "1" and attempts[conversation] == 3:
                recovery_barrier.wait()
                self.assertIsNotNone(self.bot._get_skill("read_skill_result"))
                return self.call("read_skill_result", {"step": 1, "limit": 10})
            if conversation == "2" and attempts[conversation] == 2:
                recovery_barrier.wait()
                self.assertIsNone(self.bot._get_skill("read_skill_result"))
            return self.response("Done")

        self.bot.llm.http_client.post_json.side_effect = respond
        threads = [threading.Thread(target=lambda value=value: outcomes.append(self.bot._handle_message(value, str(value))))
                   for value in (1, 2)]
        with self.assertLogs(level="WARNING"):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, [True, True])
        traces = {trace["conversation_id"]: trace for trace in self.traces}
        self.assertEqual(traces["1"]["steps"][0]["context_chars"], 2000)
        self.assertEqual(traces["1"]["steps"][1]["skill_result"].split("\n", 1)[1], "x" * 10)
        self.assertEqual(traces["2"]["steps"][0]["context_chars"], 6000)
        self.assertIsNone(self.bot.config.runtime.max_skill_result_chars)

    def test_unrelated_bad_request_does_not_trigger_context_recovery(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        self.skill.run.return_value = "x" * 5000
        self.bot.llm.http_client.post_json.side_effect = [self.call(), RuntimeError("HTTP 400 for https://example.invalid")]
        with self.assertLogs(level="ERROR"):
            self.assertFalse(self.bot._handle_message(1, "work"))
        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 2)
        self.assertEqual(self.traces[-1]["steps"][0]["context_chars"], 4000)

    def test_small_results_and_minimum_cap_do_not_retry_identical_prompts(self) -> None:
        for limit, result in ((None, "small"), (4000, "small"), (512, "x" * 5000)):
            with self.subTest(limit=limit, size=len(result)):
                self.bot.config.runtime.max_skill_result_chars = limit
                self.skill.run.return_value = result
                self.bot.llm.http_client.post_json.side_effect = [self.call(), ContextLengthExceededError("context full")]
                with self.assertLogs(level="WARNING"):
                    self.assertFalse(self.bot._handle_message(1, "work"))
                self.assertEqual(self.traces[-1]["metrics"]["request_count"], 2)
                self.assertEqual(self.traces[-1]["error"], "context_length_exceeded")

    def test_initial_capacity_error_stops_after_one_cache_retry_without_trimming_history(self) -> None:
        self.bot.llm.http_client.post_json.return_value = {
            "error": {"code": 500, "type": "server_error", "message": "Context size has been exceeded."},
        }
        with self.conversation_history() as history, self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._handle_message(1, "work"))
            self.assertEqual(list(self.bot._history[1]), history)
        calls = self.bot.llm.http_client.post_json.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[1]["messages"][1:-1], history)
        self.assertEqual(calls[1].args[1], {**calls[0].args[1], "cache_prompt": False})
        self.skill.run.assert_not_called()
        self.assertEqual(self.traces[-1]["error"], "context_length_exceeded")
        self.assertIn("context/KV-cache capacity failure", self.traces[-1]["final_reply"])
        self.assertIn("did not report enough token counts", self.traces[-1]["final_reply"])
        self.assertIn("reloading the model", self.traces[-1]["final_reply"])
        self.assertEqual(self.traces[-1]["metrics"]["requests"][0]["context_error"], {})
        self.assertNotIn("Completed actions", self.traces[-1]["final_reply"])

    def test_persistent_capacity_error_preserves_completed_results_and_history(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        self.skill.run.return_value = "x" * 6000
        error = {"error": "Context size has been exceeded."}
        self.bot.llm.http_client.post_json.side_effect = [self.call(), error, error]
        with self.conversation_history() as history, self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._handle_message(1, "work"))
            self.assertEqual(list(self.bot._history[1]), history)
        self.skill.run.assert_called_once()
        payloads = [call.args[1] for call in self.bot.llm.http_client.post_json.call_args_list]
        self.assertEqual(len(payloads), 3)
        self.assertEqual(payloads[1]["messages"][1:1 + len(history)], history)
        self.assertEqual(payloads[2], {**payloads[1], "cache_prompt": False})
        trace = self.traces[-1]
        self.assertEqual(trace["steps"][0]["context_chars"], 4000)
        self.assertEqual(trace["steps"][0]["skill_result"], "x" * 6000)
        self.assertEqual(trace["metrics"]["request_count"], 3)
        self.assertIn("context/KV-cache capacity failure", trace["final_reply"])
        self.assertIn("Completed actions", trace["final_reply"])

    def test_prompt_overflow_reports_backend_counts_without_leaking_error_body(self) -> None:
        self.bot.llm.http_client.post_json.return_value = {
            "error": {"type": "exceed_context_size_error", "n_ctx": 8192, "n_prompt_tokens": 24689,
                      "message": "SECRET prompt text"},
        }
        with self.assertLogs(level="WARNING") as logs:
            self.assertFalse(self.bot._handle_message(1, "work"))
        self.bot.llm.http_client.post_json.assert_called_once()
        trace = self.traces[-1]
        self.assertIn("24,689 prompt tokens", trace["final_reply"])
        self.assertIn("8,192-token context window", trace["final_reply"])
        self.assertEqual(trace["metrics"]["requests"][0]["context_error"], {
            "context_window_tokens": 8192, "prompt_tokens": 24689,
        })
        self.assertNotIn("SECRET", repr(trace) + "\n".join(logs.output))

    def test_output_reservation_recovers_follow_up_without_repeating_skills_or_trimming_history(self) -> None:
        self.skill.run.return_value = ("Complete result " * 1000).strip()
        self.bot.llm.http_client.post_json.side_effect = [
            self.call(),
            {"error": {"code": "context_length_exceeded", "n_ctx": 8192, "n_prompt_tokens": 6000}},
            self.response("Done"),
        ]
        with self.conversation_history() as history, self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
            self.assertEqual(list(self.bot._history[1])[:-2], history)
        self.skill.run.assert_called_once()
        calls = self.bot.llm.http_client.post_json.call_args_list
        self.assertEqual(len(calls), 3)
        first, retry = calls[1].args[1], calls[2].args[1]
        self.assertEqual(retry, {**first, "max_tokens": 2192})
        self.assertEqual(retry["messages"][1:1 + len(history)], history)
        self.assertIn(self.skill.run.return_value, retry["messages"][-1]["content"])
        trace = self.traces[-1]
        self.assertEqual(trace["final_reply"].split("\n\n", 1)[0], "Done")
        self.assertEqual(trace["steps"][0]["skill_result"], self.skill.run.return_value)
        self.assertFalse(trace["steps"][0]["result_truncated"])
        self.assertEqual(trace["metrics"]["request_count"], 3)

    def test_initial_overflow_omits_oldest_complete_turns_only_for_current_prompt(self) -> None:
        prompts = []
        replies = iter([ContextLengthExceededError("context full"), self.response("Recovered"), self.response("Next turn")])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.conversation_history() as history:
            with self.assertLogs(level="WARNING"):
                self.assertTrue(self.bot._handle_message(1, "/nothink work"))
            self.assertEqual(prompts[0][1:-1], history)
            self.assertEqual(prompts[1], [prompts[0][0], *history[4:], prompts[0][-1]])
            self.assertEqual(list(self.bot._history[1])[:-2], history)
            self.assertEqual(self.traces[-1]["last_prompt"], prompts[1])
            self.assertEqual(self.traces[-1]["metrics"]["request_count"], 2)
            self.assertEqual(self.traces[-1]["metrics"]["requests"][0]["error"], "ContextLengthExceededError")
            self.assertNotIn("stop_reason", self.traces[-1]["metrics"])
            for call in self.bot.llm.http_client.post_json.call_args_list:
                self.assertFalse(call.args[1]["chat_template_kwargs"]["enable_thinking"])
            self.assertTrue(self.bot._handle_message(1, "new work"))
            self.assertEqual(prompts[2][1:1 + len(history)], history)
        self.skill.run.assert_not_called()

    def test_initial_history_recovery_is_bounded_and_tries_without_history(self) -> None:
        prompts = []

        def reject(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            raise ContextLengthExceededError("context full")

        self.bot.llm.http_client.post_json.side_effect = reject
        with self.conversation_history(turns=8) as history, self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._handle_message(1, "work"))
        self.assertEqual([len(prompt) for prompt in prompts], [18, 10, 6, 2])
        self.assertEqual(prompts[-1], [prompts[0][0], prompts[0][-1]])
        self.assertEqual(list(self.bot._history[1]), history)
        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 4)
        self.assertEqual(self.traces[-1]["error"], "context_length_exceeded")
        self.skill.run.assert_not_called()

    def test_history_recovery_consumes_the_turn_budget(self) -> None:
        self.bot.config.runtime.max_llm_requests_per_turn = 1
        self.bot.llm.http_client.post_json.side_effect = ContextLengthExceededError("context full")
        with self.conversation_history() as history, self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._handle_message(1, "work"))
        self.bot.llm.http_client.post_json.assert_called_once()
        self.assertEqual(list(self.bot._history[1]), history)
        self.assertEqual(self.traces[-1]["error"], "request_budget_exceeded")
        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 1)
        self.skill.run.assert_not_called()

    def test_history_is_not_trimmed_for_unrelated_errors(self) -> None:
        with self.conversation_history() as history:
            for error in (RuntimeError("HTTP 400"), RequestTimeoutError("timeout")):
                with self.subTest(error=type(error).__name__):
                    self.bot.llm.http_client.post_json.reset_mock()
                    self.bot.llm.http_client.post_json.side_effect = error
                    with self.assertLogs(level="WARNING"):
                        self.assertFalse(self.bot._handle_message(1, "work"))
                    self.bot.llm.http_client.post_json.assert_called_once()
                    self.assertEqual(self.traces[-1]["last_prompt"][1:-1], history)
                    self.assertEqual(list(self.bot._history[1]), history)

    def test_follow_up_shrinks_results_before_history_and_preserves_chain_readback(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        result = "start" + "x" * 3000 + "MIDDLE EVIDENCE" + "y" * 3000 + "end"
        self.skill.run.return_value = result
        prompts = []
        caps = []
        replies = iter([
            self.call(args={"step": 1}), *[ContextLengthExceededError("context full")] * 4,
            self.call(args={"step": 2}), ContextLengthExceededError("context full"),
            self.call("read_skill_result", {"step": 1, "offset": 3005, "limit": 15}), self.response("Done"),
        ])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            caps.append([step["context_chars"] for step in self.bot._get_trace_steps()])
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.conversation_history() as history, self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertEqual(caps[1:6], [[4000], [2000], [1000], [512], [512]])
        for prompt in prompts[1:5]:
            self.assertEqual(prompt[:len(prompts[0])], prompts[0])
        self.assertEqual(prompts[5][1:5], history[4:])
        self.assertEqual(prompts[7][1:3], history[6:])
        for prompt in prompts[5:]:
            self.assertEqual(prompt[0], prompts[0][0])
            request_index = prompt.index(prompts[0][-1])
            chain = prompt[request_index + 1:]
            self.assertEqual([message["role"] for message in chain], ["assistant", "user"] * (len(chain) // 2))
            self.assertEqual(chain[0], prompts[1][-2])
            self.assertIn("Skill `echo` returned:", chain[1]["content"])
        self.assertIn("MIDDLE EVIDENCE", prompts[-1][-1]["content"])
        self.assertEqual(self.skill.run.call_count, 2)
        self.assertEqual(list(self.bot._history[1])[:-2], history)
        trace = self.traces[-1]
        self.assertEqual(trace["metrics"]["request_count"], 9)
        self.assertEqual([step["skill_result"] for step in trace["steps"][:2]], [result, result])
        self.assertEqual([step["context_chars"] for step in trace["steps"][:2]], [512, 512])

    def test_default_history_recovery_preserves_full_skill_results(self) -> None:
        self.skill.run.return_value = "x" * 6000
        self.bot.llm.http_client.post_json.side_effect = [
            self.call(), ContextLengthExceededError("context full"), self.response("Done"),
        ]
        with self.conversation_history() as history, self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        self.skill.run.assert_called_once()
        trace = self.traces[-1]
        self.assertEqual(trace["last_prompt"][1:5], history[4:])
        self.assertEqual(trace["metrics"]["request_count"], 3)
        self.assertEqual(trace["steps"][0]["context_chars"], 6000)
        self.assertFalse(trace["steps"][0]["result_truncated"])

    def test_default_recovery_compacts_results_after_history_is_exhausted(self) -> None:
        result = "x" * 6000
        self.skill.run.return_value = result
        prompts = []
        replies = iter([self.call(), *[ContextLengthExceededError("context full")] * 3, self.response("Done")])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.conversation_history(turns=2) as history, self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertEqual(prompts[2][1:3], history[2:])
        self.assertEqual(len(prompts[3]), 4)
        for prompt in prompts[1:4]:
            self.assertIn(result, prompt[-1]["content"])
        self.assertNotIn(result, prompts[4][-1]["content"])
        self.assertEqual(prompts[4][:3], prompts[3][:3])
        self.assertEqual(list(self.bot._history[1])[:-2], history)
        self.skill.run.assert_called_once()
        self.assertEqual(self.traces[-1]["steps"][0]["skill_result"], result)
        self.assertEqual(self.traces[-1]["steps"][0]["context_chars"], 2000)

    def test_follow_up_history_recovery_stops_without_replaying_actions(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        self.skill.run.return_value = "x" * 6000
        self.bot.llm.http_client.post_json.side_effect = [
            self.call(), *[ContextLengthExceededError("context full")] * 7,
        ]
        with self.conversation_history(turns=8) as history, self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._handle_message(1, "work"))
        self.skill.run.assert_called_once()
        self.assertEqual(list(self.bot._history[1]), history)
        trace = self.traces[-1]
        self.assertEqual(trace["metrics"]["request_count"], 8)
        self.assertEqual(trace["error"], "context_length_exceeded")
        self.assertEqual(len(trace["last_prompt"]), 4)
        self.assertEqual(trace["steps"][0]["skill_result"], "x" * 6000)
        self.assertIn("Completed actions", trace["final_reply"])

    def test_history_recovery_preserves_equal_current_request_and_rejects_incomplete_turns(self) -> None:
        messages = [
            {"role": "system", "content": "Keep instructions."},
            {"role": "user", "content": "work"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "work"},
        ]
        self.bot._build_messages = Mock(side_effect=lambda *_args, **_kwargs: [dict(m) for m in messages])
        self.bot.llm.http_client.post_json.side_effect = [ContextLengthExceededError("context full"), self.response("Done")]
        with self.assertLogs(level="WARNING"):
            self.assertTrue(self.bot._handle_message(1, "work"))
        self.assertEqual(self.traces[-1]["last_prompt"], [messages[0], messages[-1]])

        messages.pop(1)
        self.bot.llm.http_client.post_json.reset_mock()
        self.bot.llm.http_client.post_json.side_effect = ContextLengthExceededError("context full")
        with self.assertLogs(level="WARNING"):
            self.assertFalse(self.bot._handle_message(1, "work"))
        self.bot.llm.http_client.post_json.assert_called_once()
        self.assertEqual(self.traces[-1]["last_prompt"], messages)

    def test_history_recovery_is_isolated_between_concurrent_conversations(self) -> None:
        barrier = threading.Barrier(2, timeout=3)
        attempts = {"1": 0, "2": 0}
        outcomes = []

        def respond(_url, payload, **_kwargs):
            conversation = "1" if "- chat_id: 1\n" in payload["messages"][-1]["content"] else "2"
            attempts[conversation] += 1
            if attempts[conversation] == 1:
                barrier.wait()
                if conversation == "1":
                    raise ContextLengthExceededError("context full")
            return self.response("Done")

        self.bot.llm.http_client.post_json.side_effect = respond
        with self.conversation_history() as history, self.assertLogs(level="WARNING"):
            self.bot._history[2].extend(history)
            threads = [threading.Thread(target=lambda value=value: outcomes.append(self.bot._handle_message(value, "work")))
                       for value in (1, 2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, [True, True])
        self.assertEqual(attempts, {"1": 2, "2": 1})
        traces = {trace["conversation_id"]: trace for trace in self.traces}
        self.assertEqual(traces["1"]["last_prompt"][1:-1], history[4:])
        self.assertEqual(traces["2"]["last_prompt"][1:-1], history)
        for conversation in (1, 2):
            self.assertEqual(list(self.bot._history[conversation])[:-2], history)

    def test_scheduled_requests_recover_initial_history_overflow(self) -> None:
        slot = datetime(2026, 11, 18, 18, tzinfo=timezone.utc)
        self.bot._workspace = Mock(spec=Workspace)
        self.bot._resolve_hourly_target = Mock(return_value=None)
        reminder = {"id": "test", "backend": "telegram", "conversation_id": "1",
                    "unix_time": int(slot.timestamp()), "prompt": "work"}
        for kind, key in (("reminder", 1), ("hourly", self.bot._history_key("hourly", slot.isoformat()))):
            with self.subTest(kind=kind):
                self.bot.llm.http_client.post_json.reset_mock()
                self.bot.llm.http_client.post_json.side_effect = [
                    ContextLengthExceededError("context full"), self.response("NO_ACTION"),
                ]
                with self.conversation_history(conversation_key=key) as history, self.assertLogs(level="WARNING"):
                    if kind == "reminder":
                        self.assertTrue(self.bot._run_due_reminder(reminder))
                    else:
                        self.assertTrue(self.bot._run_hourly_task("work", slot))
                calls = self.bot.llm.http_client.post_json.call_args_list
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0].args[1]["messages"][1:-1], history)
                self.assertEqual(calls[1].args[1]["messages"][1:-1], history[4:])
        self.skill.run.assert_not_called()

    def test_reader_is_only_exposed_when_limits_are_enabled(self) -> None:
        self.assertNotIn("read_skill_result", self.bot._load_runtime_skills())
        self.bot.config.runtime.max_skill_result_chars = 512
        self.assertIn("read_skill_result", self.bot._load_runtime_skills())
        self.bot.config.runtime.max_skill_result_chars = None
        self.assertNotIn("read_skill_result", self.bot._load_runtime_skills())

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

    def test_default_web_search_recovery_does_not_replace_preview_with_larger_summary(self) -> None:
        result = "DuckDuckGo results for: test\n1. " + "x" * 6000 + "\nHTML:\n" + "y" * 6000
        web_skill = Skill("web_search", "test search", Mock(return_value=result))
        prompts = []
        replies = iter([self.call("web_search"), ContextLengthExceededError("context full"), self.call(), self.response("Done")])

        def respond(_url, payload, **_kwargs):
            prompts.append(json.loads(json.dumps(payload["messages"])))
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        with patch("messaging_llm_bot.bot.SkillManager.load", side_effect=lambda: {"echo": self.skill, "web_search": web_skill}):
            self.bot.llm.http_client.post_json.side_effect = respond
            with self.assertLogs(level="WARNING"):
                self.assertTrue(self.bot._handle_message(1, "search"))
        self.assertIn(result, prompts[1][-1]["content"])
        self.assertIn("step 1 truncated", prompts[2][-1]["content"])
        self.assertEqual(prompts[2][-1]["content"], prompts[3][-3]["content"])
        self.assertEqual(self.traces[-1]["steps"][0]["skill_result"], result)
        self.assertEqual(self.traces[-1]["steps"][0]["context_chars"], 2000)
        web_skill.run.assert_called_once()

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

    def test_hourly_context_failure_records_work_and_skips_same_slot(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        slot = datetime(2026, 11, 17, 18, tzinfo=timezone.utc)
        self.bot._current_hourly_slot = Mock(return_value=slot)
        self.bot._resolve_hourly_target = Mock(return_value=None)
        self.bot._workspace = Mock(spec=Workspace)
        self.bot._workspace.read_text.return_value = "work"
        self.bot._history[self.bot._history_key("hourly", slot.isoformat())].append(
            {"role": "user", "content": "old scheduler context"}
        )
        result = "x" * 6000
        self.skill.run.return_value = result
        self.bot.llm.http_client.post_json.side_effect = [
            self.call(), *[ContextLengthExceededError("context full")] * 4, self.response("NO_ACTION"),
        ]

        with self.assertLogs(level="WARNING") as logs:
            self.bot._poll_due_hourly_once()
            self.bot._poll_due_hourly_once()
        self.assertEqual(self.bot.llm.http_client.post_json.call_count, 5)
        self.skill.run.assert_called_once()
        self.bot._workspace.write_text.assert_not_called()
        self.bot._send_message.assert_not_called()
        self.assertNotIn(self.bot._history_key("hourly", slot.isoformat()), self.bot._history)
        self.assertTrue(any("skipping further attempts for this slot" in line for line in logs.output))
        self.assertTrue(all(record.exc_info is None for record in logs.records))
        trace = self.traces[-1]
        self.assertEqual(trace["error"], "context_length_exceeded")
        self.assertEqual(trace["conversation_id"], slot.isoformat())
        self.assertEqual(trace["steps"][0]["skill_result"], result)
        self.assertEqual(trace["metrics"]["request_count"], 5)
        self.assertEqual(trace["metrics"]["stop_reason"], "context_length_exceeded")

        next_slot = slot.replace(hour=19)
        self.bot._current_hourly_slot.return_value = next_slot
        self.bot._poll_due_hourly_once()
        self.assertEqual(self.bot.llm.http_client.post_json.call_count, 6)
        self.assertEqual(self.bot._last_hourly_slot, next_slot.isoformat())
        status = json.loads(self.bot._workspace.write_text.call_args.args[1])
        self.assertEqual(status["last_hourly_slot"], next_slot.isoformat())

    def test_hourly_context_recovery_completes_slot_without_replaying_actions(self) -> None:
        self.bot.config.runtime.max_skill_result_chars = 4000
        slot = datetime(2026, 11, 17, 18, tzinfo=timezone.utc)
        self.bot._current_hourly_slot = Mock(return_value=slot)
        self.bot._resolve_hourly_target = Mock(return_value=("telegram", "1"))
        self.bot._workspace = Mock(spec=Workspace)
        self.bot._workspace.read_text.return_value = "/nothink work"
        self.skill.run.return_value = "x" * 6000
        self.bot.llm.http_client.post_json.side_effect = [
            self.call(), *[ContextLengthExceededError("context full")] * 3, self.response("NO_ACTION"),
        ]
        with self.assertLogs(level="WARNING"):
            self.bot._poll_due_hourly_once()
            self.bot._poll_due_hourly_once()
        self.assertEqual(self.bot.llm.http_client.post_json.call_count, 5)
        self.skill.run.assert_called_once()
        self.bot._send_message.assert_not_called()
        self.assertEqual(self.bot._last_hourly_slot, slot.isoformat())
        self.assertEqual(self.bot._last_hourly_context_error_slot, "")
        self.bot._workspace.write_text.assert_called_once()
        self.bot._workspace.append_text.assert_called_once()
        for call in self.bot.llm.http_client.post_json.call_args_list:
            self.assertFalse(call.args[1]["chat_template_kwargs"]["enable_thinking"])

    def test_initial_hourly_context_failure_is_not_retried_by_polling(self) -> None:
        slot = datetime(2026, 11, 17, 18, tzinfo=timezone.utc)
        self.bot._current_hourly_slot = Mock(return_value=slot)
        self.bot._resolve_hourly_target = Mock(return_value=None)
        self.bot._workspace = Mock(spec=Workspace)
        self.bot._workspace.read_text.return_value = "work"
        self.bot.llm.http_client.post_json.side_effect = ContextLengthExceededError("context full")
        with self.assertLogs(level="WARNING"):
            self.bot._poll_due_hourly_once()
            self.bot._poll_due_hourly_once()
        self.bot.llm.http_client.post_json.assert_called_once()
        self.skill.run.assert_not_called()
        self.bot._workspace.write_text.assert_not_called()
        self.assertEqual(self.traces[-1]["steps"], [])
        self.assertEqual(self.traces[-1]["metrics"]["request_count"], 1)

    def test_other_hourly_failures_can_still_retry_in_same_slot(self) -> None:
        self.bot._resolve_hourly_target = Mock(return_value=None)
        self.bot._workspace = Mock(spec=Workspace)
        self.bot._workspace.read_text.return_value = "work"
        for hour, error in enumerate((RequestTimeoutError("timeout"), RuntimeError("unavailable")), start=18):
            with self.subTest(error=type(error).__name__):
                slot = datetime(2026, 11, 17, hour, tzinfo=timezone.utc)
                self.bot._current_hourly_slot = Mock(return_value=slot)
                self.bot.llm.http_client.post_json.reset_mock()
                self.bot.llm.http_client.post_json.side_effect = [error, self.response("NO_ACTION")]
                with self.assertLogs(level="WARNING"):
                    self.bot._poll_due_hourly_once()
                self.bot._poll_due_hourly_once()
                self.assertEqual(self.bot.llm.http_client.post_json.call_count, 2)
                self.assertEqual(self.bot._last_hourly_slot, slot.isoformat())
