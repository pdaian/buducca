import unittest
import threading
import time

from messaging_llm_bot.config import LLMConfig, LLMRunnerConfig
from messaging_llm_bot.http import RequestTimeoutError
from messaging_llm_bot.llm_client import EmptyReplyError, OpenAICompatibleClient, RequestBudgetExceeded


class StubHttpClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload, headers))
        return self.response


class SequencedHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class LLMClientTests(unittest.TestCase):
    def test_generate_reply_logs_verbose_data_when_debug_enabled(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(base_url="https://api.openai.com/v1", api_key="k", model="m")
        client = OpenAICompatibleClient(config=cfg, http_client=http, debug=True)

        with self.assertLogs(level="DEBUG") as logs:
            reply = client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "ok")
        self.assertTrue(any("LLM request payload" in line for line in logs.output))
        self.assertTrue(any("LLM response payload" in line for line in logs.output))
        self.assertTrue(any("LLM request completed in" in line for line in logs.output))

    def test_generate_reply_logs_runner_request_at_info_level(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(base_url="https://api.openai.com/v1", api_key="k", model="m")
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        with self.assertLogs(level="INFO") as logs:
            reply = client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "ok")
        self.assertTrue(any("LLM runner request: url=https://api.openai.com/v1 model=m attempt=1/1" in line for line in logs.output))

    def test_generate_reply_handles_malformed_response(self) -> None:
        http = StubHttpClient({"choices": []})
        cfg = LLMConfig(base_url="https://api.openai.com/v1", api_key="k", model="m")
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        with self.assertRaises(RuntimeError):
            client.generate_reply([{"role": "user", "content": "hi"}])

    def test_generate_reply_supports_content_parts(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]}}]})
        cfg = LLMConfig(base_url="https://api.openai.com/v1", api_key="k", model="m")
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        reply = client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "first\nsecond")

    def test_generate_reply_strips_unterminated_think_block_from_string_content(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "<think>looping forever\nstill thinking"}}]})
        cfg = LLMConfig(base_url="https://api.openai.com/v1", api_key="k", model="m")
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        with self.assertLogs(level="WARNING"), self.assertRaises(EmptyReplyError) as caught:
            client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(caught.exception.reason, "reasoning_only")
        self.assertNotIn("looping forever", str(caught.exception))
        self.assertIsNone(client.get_runner_statuses()[0].last_success_at)

    def test_empty_content_preserves_refusal(self) -> None:
        for content in (None, "", "  ", [], "<think>private reasoning</think>"):
            with self.subTest(content=content):
                client = OpenAICompatibleClient(
                    LLMConfig(base_url="https://example.invalid/v1", api_key="k", model="m"),
                    StubHttpClient({"choices": [{"message": {"content": content, "refusal": "Cannot help."}}]}),
                )
                self.assertEqual(client.generate_reply([]), "Cannot help.")

    def test_empty_truncated_reply_retries_same_prompt_with_more_output_room(self) -> None:
        messages = [{"role": "user", "content": "hello"}]
        for token_field, limit in (("max_tokens", 400), ("max_completion_tokens", 800), ("max_tokens", 8192)):
            for message in (
                {"content": None, "reasoning_content": "private reasoning"},
                {"content": "", "reasoning": "private reasoning"},
                {"content": "<think>private reasoning</think>"},
                {"content": "<think>private reasoning"},
            ):
                with self.subTest(token_field=token_field, limit=limit, message=message):
                    http = SequencedHttpClient([
                        {
                            "choices": [{"message": message, "finish_reason": "length"}],
                            "usage": {"completion_tokens": limit, "completion_tokens_details": {"reasoning_tokens": limit}},
                        },
                        {"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                         "usage": {"completion_tokens": 5}},
                    ])
                    options = {token_field: limit, "reasoning_effort": "high", "chat_template_kwargs": {"enable_thinking": True}}
                    cfg = LLMConfig(base_url="https://example.invalid/v1", api_key="k", model="m", extra_body=options)
                    client = OpenAICompatibleClient(cfg, http)

                    with self.assertLogs(level="WARNING") as logs, client.request_budget(2):
                        self.assertEqual(client.generate_reply(messages), "answer")

                    first, retry = [call[1] for call in http.calls]
                    self.assertEqual(first[token_field], limit)
                    self.assertEqual(retry, {**first, token_field: max(4096, limit * 2)})
                    self.assertEqual(retry["messages"], messages)
                    self.assertNotIn("private reasoning", repr(retry))
                    if token_field == "max_completion_tokens":
                        self.assertNotIn("max_tokens", first)
                        self.assertNotIn("max_tokens", retry)
                    self.assertEqual(cfg.extra_body[token_field], limit)
                    metrics = client.pop_last_request_metrics()
                    self.assertEqual([item["continuation"] for item in metrics], [False, False])
                    self.assertEqual([item["empty_reply_retry"] for item in metrics], [False, True])
                    self.assertEqual(metrics[0]["error"], "EmptyReplyError")
                    self.assertEqual(metrics[0]["empty_reply_reason"], "output_limit")
                    self.assertEqual(metrics[0]["usage"]["reasoning_tokens"], limit)
                    self.assertNotIn("error", metrics[1])
                    self.assertIn(f"{limit + 5} tok", client.pop_last_reply_footer())
                    self.assertTrue(any("retrying once" in line for line in logs.output))
                    self.assertIsNotNone(client.get_runner_statuses()[0].last_success_at)

    def test_empty_reply_recovery_is_bounded_and_does_not_change_later_requests(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": None}, "finish_reason": "max_tokens"}]})
        client = OpenAICompatibleClient(
            LLMConfig(base_url="https://example.invalid/v1", api_key="k", model="m", max_tokens=400), http,
        )
        with self.assertLogs(level="INFO") as logs, self.assertRaises(EmptyReplyError) as caught:
            client.generate_reply([])
        self.assertEqual(caught.exception.reason, "output_limit")
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(client.pop_last_reply_footer(), "")
        self.assertIsNone(client.get_runner_statuses()[0].last_success_at)
        self.assertEqual(client.get_runner_statuses()[0].concurrent_requests, 0)
        self.assertFalse(any("LLM request completed:" in line for line in logs.output))
        self.assertEqual([item["error"] for item in client.pop_last_request_metrics()], ["EmptyReplyError"] * 2)

        http.response = {"choices": [{"message": {"content": "next answer"}, "finish_reason": "stop"}]}
        self.assertEqual(client.generate_reply([]), "next answer")
        self.assertEqual(http.calls[-1][1]["max_tokens"], 400)

    def test_empty_replies_without_a_retryable_limit_fail_over(self) -> None:
        for finish, message, usage, reason in (
            ("stop", {"content": " "}, {}, "empty_reply"),
            ("stop", {"content": None, "reasoning": "private reasoning"}, {}, "reasoning_only"),
            ("stop", {"content": None}, {"completion_tokens_details": {"reasoning_tokens": 3}}, "reasoning_only"),
            ("content_filter", {"content": None}, {}, "content_filter"),
            ("length", {"content": None}, {}, "output_limit"),
        ):
            with self.subTest(finish=finish, reason=reason):
                http = SequencedHttpClient([
                    {"choices": [{"message": message, "finish_reason": finish}], "usage": usage},
                    {"choices": [{"message": {"content": "recovered"}, "finish_reason": "stop"}]},
                ])
                client = OpenAICompatibleClient(LLMConfig(max_tokens=None, runners=[
                    LLMRunnerConfig(base_url="https://one.invalid/v1", model="one"),
                    LLMRunnerConfig(base_url="https://two.invalid/v1", model="two"),
                ]), http)
                with self.assertLogs(level="WARNING"):
                    self.assertEqual(client.generate_reply([]), "recovered")
                self.assertEqual(len(http.calls), 2)
                metrics = client.pop_last_request_metrics()
                self.assertEqual([item["runner_index"] for item in metrics], [0, 1])
                self.assertEqual(metrics[0]["empty_reply_reason"], reason)
                self.assertIsNone(client.get_runner_statuses()[0].last_success_at)
                self.assertIsNotNone(client.get_runner_statuses()[1].last_success_at)

    def test_persistent_empty_reply_fails_over_after_one_retry(self) -> None:
        empty = {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}
        http = SequencedHttpClient([empty, empty, {"choices": [{"message": {"content": "recovered"}}]}])
        client = OpenAICompatibleClient(LLMConfig(max_tokens=400, runners=[
            LLMRunnerConfig(base_url="https://one.invalid/v1", model="one"),
            LLMRunnerConfig(base_url="https://two.invalid/v1", model="two"),
        ]), http)
        with self.assertLogs(level="WARNING"):
            self.assertEqual(client.generate_reply([]), "recovered")
        self.assertEqual([call[1]["max_tokens"] for call in http.calls], [400, 4096, 400])
        self.assertEqual([item["runner_index"] for item in client.pop_last_request_metrics()], [0, 0, 1])
        self.assertIsNone(client.get_runner_statuses()[0].last_success_at)

    def test_recovered_reply_can_still_continue_a_partial_answer(self) -> None:
        http = SequencedHttpClient([
            {"choices": [{"message": {"content": None}, "finish_reason": "length"}]},
            {"choices": [{"message": {"content": "Hello wor"}, "finish_reason": "length"}]},
            {"choices": [{"message": {"content": "ld"}, "finish_reason": "stop"}]},
        ])
        client = OpenAICompatibleClient(
            LLMConfig(base_url="https://example.invalid/v1", model="m", max_tokens=400), http,
        )
        with self.assertLogs(level="WARNING"):
            self.assertEqual(client.generate_reply([{"role": "user", "content": "hi"}]), "Hello world")
        self.assertEqual([call[1]["max_tokens"] for call in http.calls], [400, 4096, 4096])
        self.assertEqual(http.calls[2][1]["messages"][-2], {"role": "assistant", "content": "Hello wor"})
        metrics = client.pop_last_request_metrics()
        self.assertEqual([item["continuation"] for item in metrics], [False, False, True])
        self.assertEqual([item["empty_reply_retry"] for item in metrics], [False, True, False])

    def test_continuations_remain_bounded(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "part"}, "finish_reason": "length"}]})
        client = OpenAICompatibleClient(LLMConfig(base_url="https://example.invalid/v1", model="m"), http)
        with self.assertLogs(level="WARNING"):
            self.assertEqual(client.generate_reply([]), "partpartpart")
        self.assertEqual(len(http.calls), 3)

    def test_generate_reply_strips_think_text_from_content_parts(self) -> None:
        http = StubHttpClient(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "<think>private reasoning"},
                                {"type": "text", "text": "answer"},
                            ]
                        }
                    }
                ]
            }
        )
        cfg = LLMConfig(base_url="https://api.openai.com/v1", api_key="k", model="m")
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        reply = client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "answer")

    def test_generate_reply_disables_thinking_when_requested(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(base_url="https://api.openai.com/v1", api_key="k", model="m")
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        client.generate_reply([{"role": "user", "content": "hi"}], disable_thinking=True)

        self.assertEqual(http.calls[-1][1]["chat_template_kwargs"], {"enable_thinking": False})

    def test_generate_reply_merges_runner_extra_body(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(
            temperature=None,
            max_tokens=None,
            runners=[
                LLMRunnerConfig(
                    base_url="https://api.openai.com/v1",
                    api_key="k",
                    model="m",
                    extra_body={
                        "reasoning_effort": "low",
                        "max_completion_tokens": 800,
                    },
                )
            ],
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        client.generate_reply([{"role": "user", "content": "hi"}])

        payload = http.calls[-1][1]
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual(payload["max_completion_tokens"], 800)
        self.assertNotIn("temperature", payload)
        self.assertNotIn("max_tokens", payload)

    def test_disable_thinking_preserves_other_chat_template_options(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(
                    base_url="http://127.0.0.1:8000/v1",
                    api_key="k",
                    model="m",
                    extra_body={"chat_template_kwargs": {"custom_flag": True}},
                )
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        client.generate_reply([{"role": "user", "content": "hi"}], disable_thinking=True)

        self.assertEqual(
            http.calls[-1][1]["chat_template_kwargs"],
            {"custom_flag": True, "enable_thinking": False},
        )

    def test_generate_reply_disables_thinking_when_nothink_marker_is_present(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(base_url="https://api.openai.com/v1", api_key="k", model="m")
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        client.generate_reply([{"role": "user", "content": "/nothink hi"}])

        self.assertEqual(http.calls[-1][1]["chat_template_kwargs"], {"enable_thinking": False})

    def test_generate_reply_round_robins_across_configured_runners(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.1:8000/v1", api_key="k1", model="m1", model_tag="alpha"),
                LLMRunnerConfig(base_url="http://10.0.0.2:8000/v1", api_key="k2", model="m2", model_tag="beta"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        client.generate_reply([{"role": "user", "content": "hi"}])
        client.generate_reply([{"role": "user", "content": "hi"}])
        client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(
            [call[0] for call in http.calls],
            [
                "http://10.0.0.1:8000/v1/chat/completions",
                "http://10.0.0.2:8000/v1/chat/completions",
                "http://10.0.0.1:8000/v1/chat/completions",
            ],
        )
        self.assertEqual([call[1]["model"] for call in http.calls], ["m1", "m2", "m1"])

    def test_chain_runner_affinity_reuses_same_runner_across_calls(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.1:8000/v1", api_key="k1", model="m1", model_tag="alpha"),
                LLMRunnerConfig(base_url="http://10.0.0.2:8000/v1", api_key="k2", model="m2", model_tag="beta"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        with client.chain_runner_affinity():
            client.generate_reply([{"role": "user", "content": "hi"}])
            client.generate_reply([{"role": "user", "content": "again"}])

        self.assertEqual(
            [call[0] for call in http.calls],
            [
                "http://10.0.0.1:8000/v1/chat/completions",
                "http://10.0.0.1:8000/v1/chat/completions",
            ],
        )

        client.generate_reply([{"role": "user", "content": "outside"}])

        self.assertEqual(http.calls[-1][0], "http://10.0.0.2:8000/v1/chat/completions")

    def test_force_runner_routes_directly_to_requested_runner(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.1:8000/v1", api_key="k1", model="m1", model_tag="alpha"),
                LLMRunnerConfig(base_url="http://10.0.0.2:8000/v1", api_key="k2", model="m2", model_tag="beta"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        with client.force_runner(1):
            client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(
            [call[0] for call in http.calls],
            ["http://10.0.0.2:8000/v1/chat/completions"],
        )

        client.generate_reply([{"role": "user", "content": "outside"}])

        self.assertEqual(http.calls[-1][0], "http://10.0.0.1:8000/v1/chat/completions")

    def test_force_runner_rejects_unconfigured_runner_index(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.1:8000/v1", api_key="k1", model="m1", model_tag="alpha"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        with client.force_runner(1):
            with self.assertRaisesRegex(RuntimeError, r"Requested runner /1 is not configured"):
                client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(http.calls, [])

    def test_generate_reply_exposes_runner_footer(self) -> None:
        http = StubHttpClient(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"completion_tokens": 25, "total_tokens": 40},
            }
        )
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.9:8000/v1", api_key="k", model="m", model_tag="fast-a"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        reply = client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "ok")
        footer = client.pop_last_reply_footer()
        self.assertIn("IP: 10.0.0.9 | model: fast-a (chain: 0) | ", footer)
        self.assertIn("tok/s", footer)
        self.assertIn("25 tok", footer)

    def test_generate_reply_footer_reports_cache_and_reasoning_usage(self) -> None:
        http = StubHttpClient(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "completion_tokens": 25,
                    "total_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 60, "cache_write_tokens": 20},
                    "completion_tokens_details": {"reasoning_tokens": 10},
                },
            }
        )
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(
                    base_url="http://10.0.0.9:8000/v1",
                    api_key="k",
                    model="m",
                    model_tag="efficient",
                ),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        client.generate_reply([{"role": "user", "content": "hi"}])

        footer = client.pop_last_reply_footer()
        self.assertIn("cache: 60 hit, 20 write", footer)
        self.assertIn("reasoning: 10 tok", footer)
        metrics = client.pop_last_request_metrics()
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["model_tag"], "efficient")
        self.assertEqual(metrics[0]["usage"], {
            "completion_tokens": 25,
            "total_tokens": 100,
            "cached_tokens": 60,
            "cache_write_tokens": 20,
            "reasoning_tokens": 10,
        })
        self.assertGreaterEqual(metrics[0]["duration_ms"], 0)
        self.assertEqual(client.pop_last_request_metrics(), [])

    def test_generate_reply_continues_when_finish_reason_is_length(self) -> None:
        http = SequencedHttpClient(
            [
                {
                    "choices": [{"message": {"content": "Hello wor"}, "finish_reason": "length"}],
                    "usage": {"completion_tokens": 3, "total_tokens": 12},
                },
                {
                    "choices": [{"message": {"content": "ld"}, "finish_reason": "stop"}],
                    "usage": {"completion_tokens": 2, "total_tokens": 8},
                },
            ]
        )
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.9:8000/v1", api_key="k", model="m", model_tag="fast-a"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)
        client.set_active_skill_chain_length(2)

        with self.assertLogs(level="WARNING") as logs:
            reply = client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "Hello world")
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(http.calls[1][1]["messages"][-2:], [
            {"role": "assistant", "content": "Hello wor"},
            {
                "role": "user",
                "content": (
                    "Continue exactly from where you stopped. Do not repeat prior text, restart the answer, "
                    "or add commentary about continuing. Output only the remaining continuation."
                ),
            },
        ])
        footer = client.pop_last_reply_footer()
        self.assertIn("5 tok", footer)
        self.assertTrue(any("LLM completion truncated:" in line for line in logs.output))
        metrics = client.pop_last_request_metrics()
        self.assertEqual([item["continuation"] for item in metrics], [False, True])
        self.assertEqual([item["finish_reason"] for item in metrics], ["length", "stop"])
        self.assertEqual([item["usage"]["completion_tokens"] for item in metrics], [3, 2])

    def test_request_metrics_preserve_usage_before_continuation_failure_and_failover(self) -> None:
        http = SequencedHttpClient([
            {
                "choices": [{"message": {"content": "partial"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "prompt_cache_hit_tokens": 8},
            },
            RequestTimeoutError("private endpoint detail"),
            {
                "choices": [{"message": {"content": "recovered"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        ])
        client = OpenAICompatibleClient(
            config=LLMConfig(runners=[
                LLMRunnerConfig(base_url="https://primary.invalid/v1", api_key="secret", model="first"),
                LLMRunnerConfig(base_url="https://fallback.invalid/v1", api_key="secret", model="second"),
            ]),
            http_client=http,
        )

        with self.assertLogs(level="WARNING"):
            self.assertEqual(client.generate_reply([{"role": "user", "content": "hi"}]), "recovered")

        metrics = client.pop_last_request_metrics()
        self.assertEqual([item["runner_index"] for item in metrics], [0, 0, 1])
        self.assertEqual([item["continuation"] for item in metrics], [False, True, False])
        self.assertEqual(metrics[0]["usage"]["cached_tokens"], 8)
        self.assertEqual(metrics[1]["error"], "RequestTimeoutError")
        self.assertEqual(metrics[1]["usage"], {})
        self.assertEqual(metrics[2]["model"], "second")
        self.assertNotIn("secret", repr(metrics))
        self.assertNotIn("private endpoint detail", repr(metrics))

    def test_request_metrics_reset_between_calls_and_keep_usage_on_malformed_response(self) -> None:
        client = OpenAICompatibleClient(
            config=LLMConfig(base_url="https://example.invalid/v1", api_key="k", model="m"),
            http_client=SequencedHttpClient([
                {"choices": [{"message": {"content": "first"}}], "usage": {"total_tokens": 10}},
                {"choices": [], "usage": {"total_tokens": 7}},
                {"choices": [{"message": {"content": "last"}}]},
            ]),
        )
        client.generate_reply([])
        with self.assertRaises(RuntimeError):
            client.generate_reply([])
        metrics = client.pop_last_request_metrics()
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["usage"], {"total_tokens": 7})
        self.assertEqual(metrics[0]["error"], "RuntimeError")

        client.generate_reply([])
        self.assertEqual(client.pop_last_request_metrics()[0]["usage"], {})
        with client.force_runner(1):
            with self.assertRaises(RuntimeError):
                client.generate_reply([])
        self.assertEqual(client.pop_last_request_metrics(), [])

    def test_request_metrics_are_isolated_between_threads(self) -> None:
        barrier = threading.Barrier(2, timeout=2)

        class ConcurrentHttpClient:
            def post_json(self, url, payload, headers=None):
                barrier.wait()
                tokens = int(payload["messages"][0]["content"])
                return {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": tokens}}

        client = OpenAICompatibleClient(
            config=LLMConfig(base_url="https://example.invalid/v1", api_key="k", model="m"),
            http_client=ConcurrentHttpClient(),
        )
        results = {}
        errors = []

        def run(tokens):
            try:
                client.generate_reply([{"role": "user", "content": str(tokens)}])
                barrier.wait()
                results[tokens] = client.pop_last_request_metrics()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(tokens,)) for tokens in (7, 23)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        for tokens in (7, 23):
            self.assertEqual(len(results[tokens]), 1)
            self.assertEqual(results[tokens][0]["usage"], {"total_tokens": tokens})
        self.assertEqual(client.pop_last_request_metrics(), [])

    def test_chain_runner_affinity_switches_runner_after_mid_chain_failure(self) -> None:
        http = SequencedHttpClient(
            [
                {"choices": [{"message": {"content": "first"}}]},
                RuntimeError("primary unavailable"),
                {"choices": [{"message": {"content": "second"}}]},
                {"choices": [{"message": {"content": "third"}}]},
            ]
        )
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.1:8000/v1", api_key="k1", model="m1", model_tag="alpha"),
                LLMRunnerConfig(base_url="http://10.0.0.2:8000/v1", api_key="k2", model="m2", model_tag="beta"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        with client.chain_runner_affinity():
            self.assertEqual(client.generate_reply([{"role": "user", "content": "hi"}]), "first")
            self.assertEqual(client.generate_reply([{"role": "user", "content": "follow up"}]), "second")
            self.assertEqual(client.generate_reply([{"role": "user", "content": "final"}]), "third")

        self.assertEqual(
            [call[0] for call in http.calls],
            [
                "http://10.0.0.1:8000/v1/chat/completions",
                "http://10.0.0.1:8000/v1/chat/completions",
                "http://10.0.0.2:8000/v1/chat/completions",
                "http://10.0.0.2:8000/v1/chat/completions",
            ],
        )

    def test_generate_reply_fails_over_to_next_runner_when_first_runner_errors(self) -> None:
        http = SequencedHttpClient(
            [
                RuntimeError("primary unavailable"),
                {"choices": [{"message": {"content": "ok"}}], "usage": {"completion_tokens": 12}},
            ]
        )
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.1:8000/v1", api_key="k1", model="m1", model_tag="alpha"),
                LLMRunnerConfig(base_url="http://10.0.0.2:8000/v1", api_key="k2", model="m2", model_tag="beta"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        with self.assertLogs(level="WARNING") as logs:
            reply = client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "ok")
        self.assertEqual(
            [call[0] for call in http.calls],
            [
                "http://10.0.0.1:8000/v1/chat/completions",
                "http://10.0.0.2:8000/v1/chat/completions",
            ],
        )
        footer = client.pop_last_reply_footer()
        self.assertIn("IP: 10.0.0.2 | model: beta (chain: 0) | ", footer)
        self.assertIn("tok/s", footer)
        self.assertTrue(any("trying next runner" in line for line in logs.output))
        self.assertTrue(any("reason=RuntimeError: primary unavailable" in line for line in logs.output))

    def test_generate_reply_footer_falls_back_to_duration_when_usage_missing(self) -> None:
        class SlowHttpClient:
            def post_json(self, url, payload, headers=None):
                time.sleep(0.01)
                return {"choices": [{"message": {"content": "ok"}}]}

        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.9:8000/v1", api_key="k", model="m", model_tag="fast-a"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=SlowHttpClient())

        reply = client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "ok")
        footer = client.pop_last_reply_footer()
        self.assertIn("IP: 10.0.0.9 | model: fast-a (chain: 0) | ", footer)
        self.assertIn("s", footer)
        self.assertNotIn("tok/s", footer)

    def test_generate_reply_raises_after_all_runners_fail(self) -> None:
        http = SequencedHttpClient(
            [
                RuntimeError("primary unavailable"),
                RuntimeError("secondary unavailable"),
            ]
        )
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.1:8000/v1", api_key="k1", model="m1", model_tag="alpha"),
                LLMRunnerConfig(base_url="http://10.0.0.2:8000/v1", api_key="k2", model="m2", model_tag="beta"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        with self.assertLogs(level="WARNING") as logs:
            with self.assertRaisesRegex(RuntimeError, "secondary unavailable"):
                client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(len(http.calls), 2)
        self.assertEqual(client.pop_last_reply_footer(), "")
        self.assertEqual(sum("trying next runner" in line for line in logs.output), 1)
        metrics = client.pop_last_request_metrics()
        self.assertEqual([item["error"] for item in metrics], ["RuntimeError", "RuntimeError"])
        self.assertEqual([item["usage"] for item in metrics], [{}, {}])

    def test_get_runner_statuses_reports_inflight_requests(self) -> None:
        class BlockingHttpClient:
            def __init__(self) -> None:
                self.entered = threading.Event()
                self.release = threading.Event()

            def post_json(self, url, payload, headers=None):
                self.entered.set()
                self.release.wait(timeout=1.0)
                return {"choices": [{"message": {"content": "ok"}}]}

        http = BlockingHttpClient()
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.5:8000/v1", api_key="k", model="m", model_tag="pool-a"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        thread = threading.Thread(target=client.generate_reply, args=([{"role": "user", "content": "hi"}],))
        thread.start()
        self.assertTrue(http.entered.wait(timeout=0.5))
        time.sleep(0.05)

        statuses = client.get_runner_statuses()

        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].ip, "10.0.0.5")
        self.assertEqual(statuses[0].model_tag, "pool-a")
        self.assertEqual(statuses[0].concurrent_requests, 1)
        self.assertEqual(len(statuses[0].pending_for_seconds), 1)
        self.assertGreater(statuses[0].pending_for_seconds[0], 0)
        self.assertIsNone(statuses[0].last_success_at)

        http.release.set()
        thread.join(timeout=1.0)
        self.assertFalse(thread.is_alive())

        statuses = client.get_runner_statuses()
        self.assertEqual(statuses[0].concurrent_requests, 0)
        self.assertIsNotNone(statuses[0].last_success_at)


class RequestBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reply = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        self.http = SequencedHttpClient([])
        self.client = OpenAICompatibleClient(
            LLMConfig(runners=[
                LLMRunnerConfig(base_url="https://one.invalid/v1", api_key="test", model="one"),
                LLMRunnerConfig(base_url="https://two.invalid/v1", api_key="test", model="two"),
            ]), self.http,
        )
        self.messages = [{"role": "user", "content": "hello"}]

    def test_failover_cannot_bypass_budget(self) -> None:
        self.http.responses = [RequestTimeoutError("timeout"), self.reply]
        with self.client.request_budget(1), self.assertRaises(RequestBudgetExceeded):
            self.client.generate_reply(self.messages)
        self.assertEqual(len(self.http.calls), 1)
        metrics = self.client.pop_last_request_metrics()
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["error"], "RequestTimeoutError")
        self.assertTrue(all(status.concurrent_requests == 0 for status in self.client.get_runner_statuses()))

    def test_continuations_consume_the_same_budget(self) -> None:
        self.http.responses = [{"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}], "usage": {"total_tokens": 4}}, self.reply]
        with self.client.request_budget(1), self.assertRaises(RequestBudgetExceeded):
            self.client.generate_reply(self.messages)
        self.assertEqual(len(self.http.calls), 1)
        self.assertEqual(self.client.pop_last_request_metrics()[0]["usage"], {"total_tokens": 4})
        self.assertEqual(self.client.pop_last_reply_footer(), "")

    def test_empty_reply_recovery_cannot_bypass_budget_or_fail_over(self) -> None:
        self.http.responses = [
            {"choices": [{"message": {"content": None}, "finish_reason": "length"}], "usage": {"total_tokens": 4}},
            self.reply,
        ]
        with self.assertLogs(level="WARNING"), self.client.request_budget(1), self.assertRaises(RequestBudgetExceeded):
            self.client.generate_reply(self.messages)
        self.assertEqual(len(self.http.calls), 1)
        metrics = self.client.pop_last_request_metrics()
        self.assertEqual(metrics[0]["error"], "EmptyReplyError")
        self.assertEqual(metrics[0]["usage"], {"total_tokens": 4})
        self.assertEqual(self.client.pop_last_reply_footer(), "")
        self.assertTrue(all(status.last_success_at is None for status in self.client.get_runner_statuses()))

    def test_budget_spans_calls_and_is_removed_after_failure(self) -> None:
        self.http.responses = [self.reply, self.reply]
        with self.assertRaises(RequestBudgetExceeded), self.client.request_budget(1):
            self.client.generate_reply(self.messages)
            self.client.generate_reply(self.messages)
        self.assertEqual(self.client.pop_last_request_metrics(), [])
        self.assertEqual(self.client.generate_reply(self.messages), "ok")
        self.assertEqual(len(self.http.calls), 2)

    def test_failover_continuation_and_follow_up_share_one_budget(self) -> None:
        self.http.responses = [
            RequestTimeoutError("timeout"),
            {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]},
            self.reply,
        ]
        with self.client.request_budget(3):
            self.assertEqual(self.client.generate_reply(self.messages), "partialok")
            self.assertEqual(len(self.client.pop_last_request_metrics()), 3)
            with self.assertRaises(RequestBudgetExceeded):
                self.client.generate_reply(self.messages)
        self.assertEqual(len(self.http.calls), 3)

    def test_nested_budget_also_consumes_parent(self) -> None:
        self.http.responses = [self.reply] * 3
        with self.client.request_budget(2):
            with self.client.request_budget(1):
                self.client.generate_reply(self.messages)
            self.client.generate_reply(self.messages)
            with self.assertRaises(RequestBudgetExceeded):
                self.client.generate_reply(self.messages)
        self.assertEqual(len(self.http.calls), 2)

    def test_invalid_budgets_do_not_send_requests(self) -> None:
        for limit in (0, -1, True, 1.5, "2", None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                with self.client.request_budget(limit):
                    self.client.generate_reply(self.messages)
        self.assertEqual(self.http.calls, [])

    def test_malformed_responses_raise_consistent_errors_without_payloads(self) -> None:
        for payload in (None, [], {"choices": None}, {"choices": {}}, {"choices": [None]}, {"choices": [{"message": None}]}):
            with self.subTest(payload=payload):
                self.http.responses = [payload, payload]
                with self.assertRaisesRegex(RuntimeError, "Malformed response from LLM endpoint"):
                    self.client.generate_reply(self.messages)

    def test_negative_usage_is_ignored_without_masking_valid_fallbacks(self) -> None:
        counts = {}
        self.client._merge_usage_counts(counts, {
            "total_tokens": "-1", "completion_tokens": -2, "prompt_tokens": -3.0,
            "cached_tokens": -5, "prompt_tokens_details": {"cached_tokens": 4},
        })
        self.assertEqual(counts, {"cached_tokens": 4})


if __name__ == "__main__":
    unittest.main()
