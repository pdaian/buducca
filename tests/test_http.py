import socket
import unittest
from unittest.mock import patch

from telegram_llm_bot.http import HttpClient, RequestTimeoutError


class HttpClientTests(unittest.TestCase):
    def test_post_json_wraps_timeout_errors(self) -> None:
        client = HttpClient(timeout_seconds=1)
        with patch("telegram_llm_bot.http.urlopen", side_effect=TimeoutError("boom")):
            with self.assertRaises(RequestTimeoutError):
                client.post_json("https://example.com", {"a": 1})

    def test_get_bytes_wraps_socket_timeouts(self) -> None:
        client = HttpClient(timeout_seconds=1)
        with patch("telegram_llm_bot.http.urlopen", side_effect=socket.timeout("boom")):
            with self.assertRaises(RequestTimeoutError):
                client.get_bytes("https://example.com")


if __name__ == "__main__":
    unittest.main()
