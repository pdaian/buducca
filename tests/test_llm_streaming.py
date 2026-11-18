import io
import json
import threading
import unittest
from unittest.mock import patch

from messaging_llm_bot.config import LLMConfig, LLMRunnerConfig
from messaging_llm_bot.http import ContextLengthExceededError, HttpClient, RequestTimeoutError, StreamError
from messaging_llm_bot.llm_client import EmptyReplyError, OpenAICompatibleClient, RequestBudgetExceeded


def chunk(delta=None, *, finish=None, usage=None, index=0):
    return {"choices": [{"index": index, "delta": delta or {}, "finish_reason": finish}], "usage": usage}


def stream_response(events, *, done=True):
    body = b"".join(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n" for event in events)
    if done:
        body += b"data: [DONE]\n\n"
    response = io.BytesIO(body)
    response.headers = {"Content-Type": "text/event-stream"}
    return response


def json_response(data):
    response = io.BytesIO(json.dumps(data).encode("utf-8"))
    response.headers = {"Content-Type": "application/json"}
    return response


class LLMStreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = LLMConfig(base_url="http://localhost:1234/v1", api_key="SECRET", model="qwen", extra_body={})
        self.client = OpenAICompatibleClient(self.config, HttpClient(30))
        self.messages = [{"role": "user", "content": "hello"}]

    def test_stream_assembles_reply_and_reports_usage_without_double_counting(self) -> None:
        response = stream_response([
            chunk({"role": "assistant"}),
            chunk({"content": "ignored alternative"}, index=1),
            chunk({"reasoning_content": "Check the facts. "}, usage={"completion_tokens": 2}),
            chunk({"reasoning": "Compare the results."}, usage={"completion_tokens": 4}),
            chunk({"content": "Hello "}),
            chunk({"content": "café"}, finish="stop"),
            {"choices": [], "usage": {
                "prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17,
                "prompt_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 2},
                "completion_tokens_details": {"reasoning_tokens": 4},
            }},
        ])
        self.client.set_active_skill_chain_length(2)
        with patch("messaging_llm_bot.http.urlopen", return_value=response) as request, self.assertLogs(level="INFO") as logs:
            self.assertEqual(self.client.generate_reply(self.messages), "Hello café")
        payload = json.loads(request.call_args.args[0].data)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        metrics = self.client.pop_last_request_metrics()[0]
        self.assertEqual(metrics["usage"], {
            "prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17,
            "cached_tokens": 3, "cache_write_tokens": 2, "reasoning_tokens": 4,
        })
        self.assertEqual(metrics["stream_chunks"], 7)
        self.assertEqual(metrics["content_chars"], len("Hello café"))
        self.assertEqual(metrics["reasoning_chars"], len("Check the facts. Compare the results."))
        self.assertGreaterEqual(metrics["first_token_ms"], 0)
        self.assertNotIn("Check the facts", repr(metrics))
        rendered = "\n".join(logs.output)
        for detail in ("request_id=1", "chain=2", "messages=1", "prompt_chars=5", "timeout_seconds=30",
                       "LLM request first token:", "LLM request completed:", "finish_reason=stop",
                       "latest_reasoning='Check the facts. Compare the results.'", "cached_tokens=3"):
            self.assertIn(detail, rendered)
        self.assertNotIn("SECRET", rendered)
        self.assertIn("7 tok", self.client.pop_last_reply_footer())
        self.assertTrue(response.closed)

    def test_split_think_tags_are_logged_as_reasoning_and_removed_from_reply(self) -> None:
        response = stream_response([
            chunk({"content": "<th"}), chunk({"content": "ink>Consider "}),
            chunk({"content": "both options.</thi"}), chunk({"content": "nk>Answer"}, finish="stop"),
        ])
        with patch("messaging_llm_bot.http.urlopen", return_value=response), self.assertLogs(level="INFO") as logs:
            self.assertEqual(self.client.generate_reply(self.messages), "Answer")
        final = next(line for line in logs.output if "LLM request completed:" in line)
        self.assertIn("latest_reasoning='Consider both options.'", final)
        self.assertIn("latest_output='Answer'", final)
        metrics = self.client.pop_last_request_metrics()[0]
        self.assertEqual(metrics["reasoning_chars"], len("Consider both options."))
        self.assertEqual(metrics["content_chars"], 6)

    def test_reasoning_preview_is_bounded_and_control_characters_are_escaped(self) -> None:
        response = stream_response([
            chunk({"reasoning_content": "old prefix" + "r" * 800 + "\nlatest\x1b[31m"}),
            chunk({"content": "result\nnext line"}, finish="stop"),
        ])
        with patch("messaging_llm_bot.http.urlopen", return_value=response), self.assertLogs(level="INFO") as logs:
            self.client.generate_reply(self.messages)
        final = next(line for line in logs.output if "LLM request completed:" in line)
        self.assertNotIn("old prefix", final)
        self.assertNotIn("r" * 401, final)
        self.assertNotIn("\n", final)
        self.assertNotIn("\x1b", final)
        self.assertIn(r"\nlatest\x1b[31m", final)

    def test_unicode_before_think_tags_does_not_shift_preview_boundaries(self) -> None:
        response = stream_response([chunk({"content": "İ <THINK>İstanbul</THINK>answer"}, finish="stop")])
        with patch("messaging_llm_bot.http.urlopen", return_value=response), self.assertLogs(level="INFO") as logs:
            self.assertEqual(self.client.generate_reply(self.messages), "İ answer")
        final = next(line for line in logs.output if "LLM request completed:" in line)
        self.assertIn("latest_reasoning='İstanbul'", final)
        self.assertIn("latest_output='İ answer'", final)

    def test_json_fallback_keeps_full_response_compatibility(self) -> None:
        response = io.BytesIO(json.dumps({
            "choices": [{"message": {"content": "<think>Consider it.</think>Answer"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 10},
        }).encode("utf-8"))
        response.headers = {"Content-Type": "application/json"}
        with patch("messaging_llm_bot.http.urlopen", return_value=response), self.assertLogs(level="INFO") as logs:
            self.assertEqual(self.client.generate_reply(self.messages), "Answer")
        metrics = self.client.pop_last_request_metrics()[0]
        self.assertNotIn("first_token_ms", metrics)
        self.assertEqual(metrics["stream_chunks"], 0)
        final = next(line for line in logs.output if "LLM request completed:" in line)
        self.assertIn("tokens_per_second=unavailable", final)
        self.assertIn("latest_reasoning='Consider it.'", final)

    def test_stream_can_be_disabled_and_usage_options_are_not_sent(self) -> None:
        config = LLMConfig(base_url="http://localhost:1234/v1", api_key="SECRET", model="qwen", extra_body={
            "stream": False, "stream_options": {"include_usage": True},
        })
        client = OpenAICompatibleClient(config, HttpClient(30))
        with patch.object(client.http_client, "post_json", return_value={
            "choices": [{"message": {"content": "Answer", "reasoning_content": "Consider it."}}],
        }) as request, patch.object(client.http_client, "post_json_stream") as stream, self.assertLogs(level="INFO") as logs:
            self.assertEqual(client.generate_reply(self.messages), "Answer")
        stream.assert_not_called()
        self.assertFalse(request.call_args.args[1]["stream"])
        self.assertNotIn("stream_options", request.call_args.args[1])
        self.assertTrue(any("latest_reasoning='Consider it.'" in line for line in logs.output))

    def test_context_errors_in_successful_http_responses_are_classified(self) -> None:
        errors = [
            {"error": {"type": "exceed_context_size_error", "message": "SECRET prompt text"}},
            {"error": {"code": 500, "type": "server_error", "message": "Context size has been exceeded.", "details": "SECRET"}},
            {"error": "Context size has been exceeded. SECRET"},
        ]
        for streaming, json_fallback in ((False, False), (True, False), (True, True)):
            for error in errors:
                with self.subTest(streaming=streaming, json_fallback=json_fallback, error=error):
                    self.client._runners[0].extra_body = {"stream": streaming}
                    if streaming and not json_fallback:
                        response = stream_response([error])
                    else:
                        response = io.BytesIO(json.dumps(error).encode())
                        response.headers = {"Content-Type": "application/json"}
                    with patch("messaging_llm_bot.http.urlopen", return_value=response), self.assertLogs(level="WARNING") as logs:
                        with self.assertRaises(ContextLengthExceededError) as caught:
                            self.client.generate_reply(self.messages)
                    self.assertNotIn("SECRET", str(caught.exception))
                    self.assertNotIn("SECRET", "\n".join(logs.output))
                    self.assertTrue(response.closed)
                    metrics = self.client.pop_last_request_metrics()
                    self.assertEqual(len(metrics), 1)
                    self.assertEqual(metrics[0]["error"], "ContextLengthExceededError")
                    self.assertNotIn("SECRET", repr(metrics))
                    self.assertNotIn("first_token_ms", metrics[0])
                    self.assertEqual(self.client.get_runner_statuses()[0].concurrent_requests, 0)
                    self.assertIsNone(self.client.get_runner_statuses()[0].last_success_at)
                    self.assertEqual(self.client.pop_last_reply_footer(), "")

    def test_explicit_stream_options_are_preserved_without_mutating_config(self) -> None:
        options = {"include_usage": False, "custom_flag": True}
        config = LLMConfig(base_url="http://localhost:1234/v1", api_key="k", model="m", extra_body={"stream_options": options})
        response = stream_response([chunk({"content": "ok"}, finish="stop")])
        with patch("messaging_llm_bot.http.urlopen", return_value=response) as request:
            OpenAICompatibleClient(config, HttpClient(30)).generate_reply(self.messages)
        self.assertEqual(json.loads(request.call_args.args[0].data)["stream_options"], options)
        self.assertEqual(config.extra_body, {"stream_options": {"include_usage": False, "custom_flag": True}})

    def test_refusal_and_content_parts_are_preserved(self) -> None:
        for delta, expected in (({"refusal": "Cannot help."}, "Cannot help."),
                                ({"content": "", "refusal": "Cannot help."}, "Cannot help."),
                                ({"content": "<think>private</think>", "refusal": "Cannot help."}, "Cannot help."),
                                ({"content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]}, "first\nsecond")):
            with self.subTest(delta=delta), patch("messaging_llm_bot.http.urlopen", return_value=stream_response([
                chunk(delta, finish="stop"),
            ])):
                self.assertEqual(self.client.generate_reply(self.messages), expected)

    def test_reasoning_only_stream_retries_without_exposing_reasoning(self) -> None:
        for reasoning in (
            {"reasoning_content": "private reasoning"},
            {"reasoning": "private reasoning"},
            {"content": "<think>private reasoning"},
        ):
            responses = [
                stream_response([
                    chunk(reasoning, finish="length"),
                    {"choices": [], "usage": {"completion_tokens": 400, "completion_tokens_details": {"reasoning_tokens": 400}}},
                ]),
                stream_response([chunk({"content": "answer"}, finish="stop", usage={"completion_tokens": 5})]),
            ]
            with self.subTest(reasoning=reasoning), patch("messaging_llm_bot.http.urlopen", side_effect=responses) as request:
                with self.assertLogs(level="WARNING"), self.client.request_budget(2):
                    self.assertEqual(self.client.generate_reply(self.messages), "answer")
                first, retry = [json.loads(call.args[0].data) for call in request.call_args_list]
                self.assertEqual(retry, {**first, "max_tokens": first["max_tokens"] * 2})
                self.assertEqual(retry["messages"], self.messages)
                self.assertNotIn("private reasoning", repr(retry))
                metrics = self.client.pop_last_request_metrics()
                self.assertEqual(metrics[0]["error"], "EmptyReplyError")
                self.assertEqual(metrics[0]["finish_reason"], "length")
                self.assertEqual(metrics[0]["usage"]["reasoning_tokens"], 400)
                self.assertEqual(metrics[0]["reasoning_chars"], len("private reasoning"))
                self.assertTrue(metrics[1]["empty_reply_retry"])
                self.assertIn("405 tok", self.client.pop_last_reply_footer())
                self.assertTrue(all(response.closed for response in responses))

    def test_empty_json_fallback_is_a_failed_request(self) -> None:
        response = io.BytesIO(json.dumps({
            "choices": [{"message": {"content": None, "reasoning": "private reasoning"}, "finish_reason": "stop"}],
        }).encode())
        response.headers = {"Content-Type": "application/json"}
        with patch("messaging_llm_bot.http.urlopen", return_value=response), self.assertLogs(level="INFO") as logs:
            with self.assertRaises(EmptyReplyError) as caught:
                self.client.generate_reply(self.messages)
        self.assertEqual(caught.exception.reason, "reasoning_only")
        self.assertFalse(any("LLM request completed:" in line for line in logs.output))
        self.assertIsNone(self.client.get_runner_statuses()[0].last_success_at)
        self.assertTrue(response.closed)

    def test_second_empty_reply_retry_preserves_streaming_and_context_errors(self) -> None:
        for context_error in (False, True):
            with self.subTest(context_error=context_error):
                self.config.extra_body = {"max_completion_tokens": 4096, "reasoning_effort": "high"}
                client = OpenAICompatibleClient(self.config, HttpClient(30))
                responses = [
                    stream_response([
                        chunk({"reasoning_content": "private reasoning"}, finish="length"),
                        {"choices": [], "usage": {"completion_tokens": tokens}},
                    ]) for tokens in (4096, 8192)
                ] + [stream_response(
                    [{"error": {"type": "exceed_context_size_error", "message": "SECRET"}}]
                    if context_error else [chunk({"content": "answer"}, finish="stop")]
                )]
                with patch("messaging_llm_bot.http.urlopen", side_effect=responses) as request, self.assertLogs(level="WARNING"):
                    with client.request_budget(3):
                        if context_error:
                            with self.assertRaises(ContextLengthExceededError):
                                client.generate_reply(self.messages)
                        else:
                            self.assertEqual(client.generate_reply(self.messages), "answer")
                payloads = [json.loads(call.args[0].data) for call in request.call_args_list]
                self.assertEqual(payloads, [
                    {**payloads[0], "max_completion_tokens": tokens} for tokens in (4096, 8192, 32768)
                ])
                self.assertEqual(payloads[0]["messages"], self.messages)
                self.assertEqual(payloads[0]["reasoning_effort"], "high")
                self.assertTrue(all(payload["stream"] for payload in payloads))
                self.assertTrue(all("max_tokens" not in payload for payload in payloads))
                metrics = client.pop_last_request_metrics()
                self.assertEqual([item["max_completion_tokens"] for item in metrics], [4096, 8192, 32768])
                self.assertEqual([item["empty_reply_retry"] for item in metrics], [False, True, True])
                self.assertEqual([item["usage"] for item in metrics], [{"completion_tokens": 4096}, {"completion_tokens": 8192}, {}])
                self.assertNotIn("private reasoning", repr(metrics))
                self.assertTrue(all(response.closed for response in responses))
                if context_error:
                    self.assertEqual(metrics[-1]["error"], "ContextLengthExceededError")
                    self.assertEqual(client.pop_last_reply_footer(), "")
                    self.assertIsNone(client.get_runner_statuses()[0].last_success_at)
                else:
                    self.assertNotIn("error", metrics[-1])
                    self.assertIsNotNone(client.get_runner_statuses()[0].last_success_at)

    def test_continuation_keeps_metrics_and_consumes_request_budget(self) -> None:
        responses = [
            stream_response([chunk({"content": "Hello wor"}, finish="length", usage={"completion_tokens": 2})]),
            stream_response([chunk({"content": "ld"}, finish="stop", usage={"completion_tokens": 3})]),
        ]
        with patch("messaging_llm_bot.http.urlopen", side_effect=responses) as request, self.assertLogs(level="INFO") as logs:
            with self.client.request_budget(2):
                self.assertEqual(self.client.generate_reply(self.messages), "Hello world")
                metrics = self.client.pop_last_request_metrics()
                footer = self.client.pop_last_reply_footer()
                with self.assertRaises(RequestBudgetExceeded):
                    self.client.generate_reply(self.messages)
        self.assertEqual(request.call_count, 2)
        self.assertEqual([item["request_id"] for item in metrics], [1, 2])
        self.assertEqual([item["continuation"] for item in metrics], [False, True])
        self.assertEqual([item["usage"]["completion_tokens"] for item in metrics], [2, 3])
        self.assertIn("5 tok", footer)
        self.assertTrue(any("continuation=1/2" in line for line in logs.output))

    def test_failed_stream_retries_same_prompt_without_streaming(self) -> None:
        options = {
            "stream": True, "stream_options": {"include_usage": True},
            "max_completion_tokens": 8192, "reasoning_effort": "high",
            "chat_template_kwargs": {"enable_thinking": True, "custom_flag": True},
        }
        self.client._runners[0].extra_body = options
        for tail, done in (([{"error": {"message": "PRIVATE server error"}}], True), ([], False), ([], True)):
            with self.subTest(tail=tail, done=done):
                responses = [
                    stream_response([
                        chunk({"reasoning_content": "private reasoning"}, usage={"completion_tokens": 400}),
                        chunk({"content": "discard this incomplete answer"}),
                        *tail,
                    ], done=done),
                    json_response({
                        "choices": [{"message": {"content": "Recovered"}, "finish_reason": "stop"}],
                        "usage": {"completion_tokens": 5},
                    }),
                ]
                with patch("messaging_llm_bot.http.urlopen", side_effect=responses) as request, self.assertLogs(level="INFO") as logs:
                    with self.client.request_budget(2):
                        self.assertEqual(self.client.generate_reply(self.messages), "Recovered")
                first, retry = [json.loads(call.args[0].data) for call in request.call_args_list]
                self.assertEqual(retry, {**{key: value for key, value in first.items() if key != "stream_options"}, "stream": False})
                self.assertEqual(retry["messages"], self.messages)
                self.assertNotIn("private reasoning", repr(retry))
                self.assertIs(self.client._runners[0].extra_body, options)
                self.assertTrue(options["stream"])
                metrics = self.client.pop_last_request_metrics()
                self.assertEqual([item["stream"] for item in metrics], [True, False])
                self.assertEqual([item["stream_fallback"] for item in metrics], [False, True])
                self.assertEqual(metrics[0]["error"], "StreamError")
                self.assertEqual(metrics[0]["usage"], {"completion_tokens": 400})
                self.assertNotIn("error", metrics[1])
                self.assertIn("405 tok", self.client.pop_last_reply_footer())
                self.assertEqual(sum("LLM request completed:" in line for line in logs.output), 1)
                self.assertNotIn("PRIVATE", "\n".join(logs.output))
                self.assertTrue(all(response.closed for response in responses))

        response = stream_response([chunk({"content": "Next answer"}, finish="stop")])
        with patch("messaging_llm_bot.http.urlopen", return_value=response) as request:
            self.assertEqual(self.client.generate_reply(self.messages), "Next answer")
        self.assertTrue(json.loads(request.call_args.args[0].data)["stream"])
        self.assertFalse(self.client.pop_last_request_metrics()[0]["stream_fallback"])

    def test_stream_recovery_is_bounded_and_fails_over_after_failed_fallback(self) -> None:
        self.config.runners = [
            LLMRunnerConfig(base_url=f"http://runner{index}.invalid/v1", api_key="k", model=f"m{index}")
            for index in range(2)
        ]
        client = OpenAICompatibleClient(self.config, HttpClient(30))
        responses = [
            stream_response([chunk({"content": "partial"}, usage={"completion_tokens": 2})], done=False),
            json_response({"error": {"message": "PRIVATE server error"}}),
            stream_response([chunk({"content": "recovered"}, finish="stop", usage={"completion_tokens": 3})]),
        ]
        with patch("messaging_llm_bot.http.urlopen", side_effect=responses) as request, self.assertLogs(level="INFO") as logs:
            self.assertEqual(client.generate_reply(self.messages), "recovered")
        self.assertEqual(request.call_count, 3)
        metrics = client.pop_last_request_metrics()
        self.assertEqual(metrics[0]["error"], "StreamError")
        self.assertEqual(metrics[0]["usage"], {"completion_tokens": 2})
        self.assertEqual(metrics[0]["content_chars"], 7)
        self.assertEqual([item["runner_index"] for item in metrics], [0, 0, 1])
        self.assertEqual([item["stream"] for item in metrics], [True, False, True])
        self.assertEqual(metrics[1]["error"], "RuntimeError")
        self.assertIsNone(client.get_runner_statuses()[0].last_success_at)
        self.assertNotIn("PRIVATE", "\n".join(logs.output))
        self.assertEqual(sum("LLM request completed:" in line for line in logs.output), 1)
        self.assertTrue(all(response.closed for response in responses))

    def test_stream_recovery_consumes_request_budget(self) -> None:
        response = stream_response([chunk({"reasoning": "private reasoning"})], done=False)
        with patch("messaging_llm_bot.http.urlopen", return_value=response) as request, self.assertLogs(level="WARNING"):
            with self.client.request_budget(1), self.assertRaises(RequestBudgetExceeded):
                self.client.generate_reply(self.messages)
        request.assert_called_once()
        self.assertEqual(self.client.pop_last_request_metrics()[0]["error"], "StreamError")
        self.assertEqual(self.client.pop_last_reply_footer(), "")
        self.assertIsNone(self.client.get_runner_statuses()[0].last_success_at)
        self.assertEqual(self.client.get_runner_statuses()[0].concurrent_requests, 0)
        self.assertTrue(response.closed)

    def test_nonstreaming_fallback_preserves_context_error_and_failure_status(self) -> None:
        for error, exception in (
            ({"error": {"message": "PRIVATE server error"}}, RuntimeError),
            ({"error": {"message": "Context size has been exceeded. PRIVATE"}}, ContextLengthExceededError),
        ):
            responses = [stream_response([], done=False), json_response(error)]
            with self.subTest(error=error), patch("messaging_llm_bot.http.urlopen", side_effect=responses) as request, self.assertLogs(level="INFO") as logs:
                with self.assertRaises(exception) as caught:
                    self.client.generate_reply(self.messages)
            self.assertNotIsInstance(caught.exception, StreamError)
            self.assertEqual(request.call_count, 2)
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertNotIn("PRIVATE", "\n".join(logs.output))
            self.assertFalse(any("LLM request completed:" in line for line in logs.output))
            metrics = self.client.pop_last_request_metrics()
            self.assertEqual([item["error"] for item in metrics], ["StreamError", exception.__name__])
            self.assertIsNone(self.client.get_runner_statuses()[0].last_success_at)
            self.assertEqual(self.client.get_runner_statuses()[0].concurrent_requests, 0)
            self.assertEqual(self.client.pop_last_reply_footer(), "")
            self.assertTrue(all(response.closed for response in responses))

    def test_nonstreaming_fallback_keeps_continuation_and_empty_reply_recovery(self) -> None:
        responses = [
            stream_response([chunk({"reasoning": "private reasoning"})], done=False),
            json_response({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}),
            json_response({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}),
            json_response({"choices": [{"message": {"content": "Hello wor"}, "finish_reason": "length"}]}),
            json_response({"choices": [{"message": {"content": "ld"}, "finish_reason": "stop"}]}),
        ]
        with patch("messaging_llm_bot.http.urlopen", side_effect=responses) as request, self.assertLogs(level="WARNING"):
            with self.client.request_budget(5):
                self.assertEqual(self.client.generate_reply(self.messages), "Hello world")
        payloads = [json.loads(call.args[0].data) for call in request.call_args_list]
        self.assertEqual([payload["stream"] for payload in payloads], [True, False, False, False, False])
        self.assertEqual([payload["max_tokens"] for payload in payloads], [4096, 4096, 8192, 32768, 32768])
        self.assertEqual(payloads[4]["messages"][-2], {"role": "assistant", "content": "Hello wor"})
        metrics = self.client.pop_last_request_metrics()
        self.assertEqual([item["continuation"] for item in metrics], [False, False, False, False, True])
        self.assertEqual([item["empty_reply_retry"] for item in metrics], [False, False, True, True, False])
        self.assertTrue(all(response.closed for response in responses))

    def test_stream_timeout_keeps_configured_timeout_and_does_not_trigger_fallback(self) -> None:
        with patch("messaging_llm_bot.http.urlopen", side_effect=TimeoutError("PRIVATE")) as request, self.assertLogs(level="WARNING"):
            with self.assertRaises(RequestTimeoutError):
                self.client.generate_reply(self.messages)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs["timeout"], 30)
        self.assertEqual(self.client.pop_last_request_metrics()[0]["error"], "RequestTimeoutError")

    def test_malformed_or_error_streams_never_report_success(self) -> None:
        for events in ([chunk({"content": "partial"})], [], [{"choices": None}],
                       [{"choices": [{"index": 0, "delta": "bad"}]}],
                       [{"error": {"message": "PRIVATE error body"}}]):
            response = stream_response(events)
            with self.subTest(events=events), patch("messaging_llm_bot.http.urlopen", return_value=response), patch.object(
                self.client.http_client, "post_json", return_value={"error": {"message": "PRIVATE error body"}},
            ), self.assertLogs(level="INFO") as logs:
                with self.assertRaises(RuntimeError) as caught:
                    self.client.generate_reply(self.messages)
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertFalse(any("LLM request completed:" in line for line in logs.output))
            self.assertTrue(any("LLM request failed:" in line for line in logs.output))
            self.assertEqual(self.client.get_runner_statuses()[0].concurrent_requests, 0)
            self.assertTrue(response.closed)

    def test_heartbeats_continue_while_waiting_and_thinking_and_stop_on_exit(self) -> None:
        for fail in (False, True):
            waiting_seen = threading.Event()
            thinking_seen = threading.Event()
            progress_logs = []

            def record_log(_level, template, *args):
                line = template % args
                progress_logs.append(line)
                if "LLM request progress:" in line:
                    if "phase=waiting_for_output" in line:
                        waiting_seen.set()
                    if "phase=thinking" in line and "latest_reasoning='Considering options.'" in line:
                        thinking_seen.set()

            class SlowHttpClient:
                closed = False

                def post_json_stream(self, _url, _payload, headers=None):
                    try:
                        if not waiting_seen.wait(2):
                            raise AssertionError("No heartbeat before the first token")
                        yield chunk({"reasoning_content": "Considering options."})
                        if not thinking_seen.wait(2):
                            raise AssertionError("No heartbeat during reasoning")
                        if fail:
                            raise RequestTimeoutError("test timeout")
                        yield chunk({"content": "Answer"}, finish="stop")
                    finally:
                        self.closed = True

            http = SlowHttpClient()
            client = OpenAICompatibleClient(self.config, http)
            with self.subTest(fail=fail), self.assertLogs(level="INFO"), patch(
                "messaging_llm_bot.llm_progress._PROGRESS_INTERVAL_SECONDS", 0.01,
            ), patch("messaging_llm_bot.llm_progress.logging.log", side_effect=record_log):
                if fail:
                    with self.assertRaises(RequestTimeoutError):
                        client.generate_reply(self.messages)
                else:
                    self.assertEqual(client.generate_reply(self.messages), "Answer")
            self.assertTrue(waiting_seen.is_set())
            self.assertTrue(thinking_seen.is_set())
            self.assertTrue(http.closed)
            self.assertFalse(any(thread.name.startswith("llm-progress-") for thread in threading.enumerate()))
            self.assertIn("failed:" if fail else "completed:", progress_logs[-1])
            self.assertIn("idle=", progress_logs[-1])

    def test_concurrent_streams_keep_usage_and_previews_separate(self) -> None:
        barrier = threading.Barrier(2, timeout=2)
        results = {}
        errors = []

        class ConcurrentHttpClient:
            def post_json_stream(self, _url, payload, headers=None):
                tokens = int(payload["messages"][0]["content"])
                yield chunk({"reasoning_content": f"reasoning for {tokens}"})
                barrier.wait()
                yield chunk({"content": f"answer for {tokens}"}, finish="stop", usage={"completion_tokens": tokens})

        client = OpenAICompatibleClient(self.config, ConcurrentHttpClient())

        def run(tokens):
            try:
                reply = client.generate_reply([{"role": "user", "content": str(tokens)}])
                results[tokens] = (reply, client.pop_last_request_metrics()[0])
            except Exception as exc:
                errors.append(exc)

        with self.assertLogs(level="INFO") as logs:
            threads = [threading.Thread(target=run, args=(tokens,)) for tokens in (7, 23)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len({metrics["request_id"] for _, metrics in results.values()}), 2)
        for tokens, (reply, metrics) in results.items():
            self.assertEqual(reply, f"answer for {tokens}")
            self.assertEqual(metrics["usage"], {"completion_tokens": tokens})
            final = next(line for line in logs.output if "LLM request completed:" in line and f"request_id={metrics['request_id']} " in line)
            self.assertIn(f"latest_reasoning='reasoning for {tokens}'", final)
            self.assertIn(f"latest_output='answer for {tokens}'", final)
        self.assertEqual(client.pop_last_request_metrics(), [])
        self.assertFalse(any(thread.name.startswith("llm-progress-") for thread in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()
