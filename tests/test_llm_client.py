import unittest
import threading
import time

from messaging_llm_bot.config import LLMConfig, LLMRunnerConfig
from messaging_llm_bot.llm_client import OpenAICompatibleClient


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

        reply = client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "")

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
        self.assertIn("IP: 10.0.0.9 | model: fast-a | ", footer)
        self.assertIn("tok/s", footer)
        self.assertIn("25 tok", footer)

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
        self.assertIn("IP: 10.0.0.2 | model: beta | ", footer)
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
        self.assertIn("IP: 10.0.0.9 | model: fast-a | ", footer)
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


if __name__ == "__main__":
    unittest.main()
