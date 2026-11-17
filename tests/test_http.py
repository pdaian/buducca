import io
import json
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

    def test_stream_parses_events_incrementally_and_closes_response(self) -> None:
        body = (
            b': heartbeat\r\nevent: message\r\ndata: {"choices": [],\r\ndata: "usage": {"total_tokens": 3}}\r\n\r\n'
            + 'data: {"text": "café"}\n\ndata: [DONE]\n\n'.encode("utf-8")
        )
        response = io.BytesIO(body)
        response.headers = {"Content-Type": "text/event-stream; charset=utf-8"}
        with patch("messaging_llm_bot.http.urlopen", return_value=response) as request:
            events = HttpClient(7).post_json_stream("https://example.invalid", {"stream": True}, {"Authorization": "Bearer k"})
            self.assertEqual(next(events), {"choices": [], "usage": {"total_tokens": 3}})
            self.assertLess(response.tell(), len(body))
            self.assertEqual(list(events), [{"text": "café"}])
        self.assertTrue(response.closed)
        req = request.call_args.args[0]
        self.assertEqual(req.get_header("Accept"), "text/event-stream")
        self.assertEqual(req.get_header("Authorization"), "Bearer k")
        self.assertEqual(json.loads(req.data), {"stream": True})
        self.assertEqual(request.call_args.kwargs["timeout"], 7)

    def test_stream_accepts_json_when_server_does_not_stream(self) -> None:
        response = io.BytesIO(b'{"choices": [{"message": {"content": "ok"}}]}')
        response.headers = {"Content-Type": "application/json; charset=utf-8"}
        with patch("messaging_llm_bot.http.urlopen", return_value=response):
            self.assertEqual(list(HttpClient(1).post_json_stream("https://example.invalid", {})), [
                {"choices": [{"message": {"content": "ok"}}]},
            ])
        self.assertTrue(response.closed)

    def test_stream_rejects_truncated_or_malformed_events_without_body_leaks(self) -> None:
        for body in (b'data: {}\n\n', b'data: {"unfinished":', b'data: SECRET\n\n', b'data: []\n\n', b'data: \xffSECRET\n\n'):
            response = io.BytesIO(body)
            response.headers = {"Content-Type": "text/event-stream"}
            with self.subTest(body=body), patch("messaging_llm_bot.http.urlopen", return_value=response):
                with self.assertRaises(RuntimeError) as caught:
                    list(HttpClient(1).post_json_stream("https://user:password@example.invalid?key=PRIVATE", {}))
                for secret in ("SECRET", "PRIVATE", "password", "user:"):
                    self.assertNotIn(secret, str(caught.exception))
            self.assertTrue(response.closed)

    def test_stream_timeout_during_read_is_wrapped_and_response_is_closed(self) -> None:
        class TimeoutResponse(io.BytesIO):
            def __next__(self):
                raise URLError(TimeoutError("PRIVATE"))

        response = TimeoutResponse()
        response.headers = {"Content-Type": "text/event-stream"}
        with patch("messaging_llm_bot.http.urlopen", return_value=response):
            with self.assertRaises(RequestTimeoutError) as caught:
                list(HttpClient(1).post_json_stream("https://example.invalid?key=PRIVATE", {}))
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertTrue(response.closed)

    def test_stream_consumer_can_close_before_done(self) -> None:
        response = io.BytesIO(b'data: {}\n\ndata: [DONE]\n\n')
        response.headers = {"Content-Type": "text/event-stream"}
        with patch("messaging_llm_bot.http.urlopen", return_value=response):
            events = HttpClient(1).post_json_stream("https://example.invalid", {})
            self.assertEqual(next(events), {})
            events.close()
        self.assertTrue(response.closed)

    def test_stream_rejects_nonfinite_json_before_network_io(self) -> None:
        with patch("messaging_llm_bot.http.urlopen") as request:
            with self.assertRaises(ValueError):
                list(HttpClient(1).post_json_stream("https://example.invalid", {"temperature": float("nan")}))
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
