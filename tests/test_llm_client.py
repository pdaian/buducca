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

    def test_generate_reply_exposes_runner_footer(self) -> None:
        http = StubHttpClient({"choices": [{"message": {"content": "ok"}}]})
        cfg = LLMConfig(
            runners=[
                LLMRunnerConfig(base_url="http://10.0.0.9:8000/v1", api_key="k", model="m", model_tag="fast-a"),
            ]
        )
        client = OpenAICompatibleClient(config=cfg, http_client=http)

        reply = client.generate_reply([{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "ok")
        self.assertEqual(client.pop_last_reply_footer(), "IP: 10.0.0.9 | model: fast-a")

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

        http.release.set()
        thread.join(timeout=1.0)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
