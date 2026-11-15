import io
import socket
import traceback
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from messaging_llm_bot.http import HttpClient, RequestTimeoutError


class HttpClientTests(unittest.TestCase):
    def test_post_json_wraps_timeout_errors(self) -> None:
        client = HttpClient(timeout_seconds=1)
        with patch("messaging_llm_bot.http.urlopen", side_effect=TimeoutError("boom")):
            with self.assertRaises(RequestTimeoutError):
                client.post_json("https://example.com", {"a": 1})

    def test_get_bytes_wraps_socket_timeouts(self) -> None:
        client = HttpClient(timeout_seconds=1)
        with patch("messaging_llm_bot.http.urlopen", side_effect=socket.timeout("boom")):
            with self.assertRaises(RequestTimeoutError):
                client.get_bytes("https://example.com")

    def test_urlerror_wrapped_timeout_is_classified_consistently(self) -> None:
        client = HttpClient(timeout_seconds=1)
        for operation in (client.get_bytes, client.get_json):
            with self.subTest(operation=operation.__name__), patch(
                "messaging_llm_bot.http.urlopen", side_effect=URLError(TimeoutError("timeout")),
            ):
                with self.assertRaises(RequestTimeoutError):
                    operation("https://example.invalid")

    def test_errors_do_not_expose_credentials_or_response_bodies(self) -> None:
        url = "https://user:password@api.telegram.org/botSECRET/getUpdates?key=PRIVATE"
        errors = [
            HTTPError(url, 409, "conflict", {}, io.BytesIO(b"SECRET PRIVATE password")),
            URLError("SECRET PRIVATE password"),
            TimeoutError("SECRET PRIVATE password"),
        ]
        client = HttpClient(timeout_seconds=1)
        for error in errors:
            with self.subTest(error=type(error).__name__), patch("messaging_llm_bot.http.urlopen", side_effect=error):
                try:
                    client.get_json(url)
                except RuntimeError as exc:
                    rendered = "".join(traceback.format_exception(exc))
                    self.assertIn("/getUpdates", str(exc))
                    for secret in ("SECRET", "PRIVATE", "password", "user:"):
                        self.assertNotIn(secret, rendered)
                else:
                    self.fail("Expected a transport error")
        self.assertTrue(errors[0].closed)

    def test_invalid_json_shapes_and_encoding_are_rejected_without_body_leaks(self) -> None:
        client = HttpClient(timeout_seconds=1)
        for body in (b"[]", b"null", b'"SECRET"', b"SECRET", b"\xffSECRET"):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)
            response.read.return_value = body
            with self.subTest(body=body), patch("messaging_llm_bot.http.urlopen", return_value=response):
                with self.assertRaises(RuntimeError) as caught:
                    client.get_json("https://example.invalid?secret=PRIVATE")
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertNotIn("PRIVATE", str(caught.exception))

    def test_nonfinite_request_json_is_rejected_before_network_io(self) -> None:
        with patch("messaging_llm_bot.http.urlopen") as request:
            with self.assertRaises(ValueError):
                HttpClient(1).post_json("https://example.invalid", {"temperature": float("nan")})
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
