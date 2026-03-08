import unittest

from messaging_llm_bot.config import LLMConfig
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


if __name__ == "__main__":
    unittest.main()
