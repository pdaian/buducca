import io
import json
import socket
import traceback
import unittest
from http.client import IncompleteRead, RemoteDisconnected
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from messaging_llm_bot.http import ContextLengthExceededError, HttpClient, RequestTimeoutError, StreamError


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

    def test_context_errors_are_classified_without_exposing_response_bodies(self) -> None:
        url = "https://user:password@example.invalid/v1/chat/completions?key=PRIVATE"
        bodies = [
            {"error": {"code": "context_length_exceeded", "message": "SECRET password"}},
            {"error": {"code": 400, "type": "exceed_context_size_error", "n_prompt_tokens": 24689, "n_ctx": 8192}},
            {"error": "request (24689 tokens) exceeds the available context size (8192 tokens), try increasing it SECRET"},
            {"error": {"message": "Trying to keep the first 10000 tokens when context the length is only 8192"}},
            {"error": {"message": "This model's maximum context length is 8192 tokens. However, you requested 10000 tokens."}},
        ]
        for streaming in (False, True):
            for body in bodies:
                response = io.BytesIO(json.dumps(body).encode())
                error = HTTPError(url, 400, "Bad Request", {}, response)
                with self.subTest(streaming=streaming, body=body), patch("messaging_llm_bot.http.urlopen", side_effect=error):
                    with self.assertRaises(ContextLengthExceededError) as caught:
                        client = HttpClient(1)
                        if streaming:
                            list(client.post_json_stream(url, {}))
                        else:
                            client.post_json(url, {})
                rendered = "".join(traceback.format_exception(caught.exception))
                self.assertIn("model context window exceeded", rendered)
                for secret in ("SECRET", "PRIVATE", "password", "user:"):
                    self.assertNotIn(secret, rendered)
                self.assertTrue(response.closed)

    def test_unrelated_bad_requests_remain_generic_and_body_reads_are_bounded(self) -> None:
        for body in (b'{"error":{"message":"Unknown model SECRET"}}', b'[]', b'null', b'\xffSECRET', b'x' * 100000):
            response = io.BytesIO(body)
            error = HTTPError("https://example.invalid", 400, "Bad Request", {}, response)
            with self.subTest(body=body[:100]), patch("messaging_llm_bot.http.urlopen", side_effect=error), patch.object(error, "read", wraps=error.read) as read:
                with self.assertRaises(RuntimeError) as caught:
                    HttpClient(1).post_json("https://example.invalid", {})
                self.assertNotIsInstance(caught.exception, ContextLengthExceededError)
                self.assertEqual(str(caught.exception), "HTTP 400 for https://example.invalid")
                read.assert_called_once_with(8192)
            self.assertTrue(response.closed)

    def test_http_500_context_errors_are_classified_without_treating_other_failures_as_overflow(self) -> None:
        url = "https://user:password@example.invalid/v1/chat/completions?key=PRIVATE"
        bodies = [
            (b'{"error":{"code":500,"message":"Context size has been exceeded.","type":"server_error"}}', True),
            (b'{"error":"Context length has been exceeded. SECRET"}', True),
            (b'{"error":{"code":500,"message":"Compute error. SECRET","type":"server_error"}}', False),
            (b'{"error":{"code":500,"message":"Failed to allocate KV cache. SECRET","type":"server_error"}}', False),
            (b'{"error":{"message":null,"type":"server_error"}}', False),
            (b'[]', False),
            (b'\xffSECRET', False),
            (b'x' * 100000, False),
        ]
        for streaming in (False, True):
            for body, is_context_error in bodies:
                response = io.BytesIO(body)
                error = HTTPError(url, 500, "Internal Server Error", {}, response)
                with self.subTest(streaming=streaming, body=body[:150]), patch(
                    "messaging_llm_bot.http.urlopen", side_effect=error,
                ), patch.object(error, "read", wraps=error.read) as read:
                    with self.assertRaises(RuntimeError) as caught:
                        client = HttpClient(1)
                        if streaming:
                            list(client.post_json_stream(url, {}))
                        else:
                            client.post_json(url, {})
                    read.assert_called_once_with(8192)
                self.assertEqual(isinstance(caught.exception, ContextLengthExceededError), is_context_error)
                expected = "HTTP 500 for https://example.invalid/v1/chat/completions"
                if is_context_error:
                    expected += ": model context window exceeded"
                self.assertEqual(str(caught.exception), expected)
                rendered = "".join(traceback.format_exception(caught.exception))
                for secret in ("SECRET", "PRIVATE", "password", "user:"):
                    self.assertNotIn(secret, rendered)
                self.assertTrue(response.closed)

    def test_failed_error_body_read_preserves_http_status_and_closes_response(self) -> None:
        for failure in (TimeoutError("SECRET"), IncompleteRead(b"SECRET")):
            error = HTTPError("https://example.invalid", 400, "Bad Request", {}, io.BytesIO())
            with self.subTest(failure=type(failure).__name__), patch("messaging_llm_bot.http.urlopen", side_effect=error), patch.object(error, "read", side_effect=failure):
                with self.assertRaisesRegex(RuntimeError, "^HTTP 400 for https://example.invalid$"):
                    HttpClient(1).post_json("https://example.invalid", {})
            self.assertTrue(error.closed)

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

    def test_stream_disconnects_are_classified_without_leaking_transport_details(self) -> None:
        for failure in (IncompleteRead(b"PRIVATE"), RemoteDisconnected("PRIVATE"), ConnectionResetError("PRIVATE")):
            class InterruptedResponse(io.BytesIO):
                def __next__(self):
                    raise failure

            response = InterruptedResponse()
            response.headers = {"Content-Type": "text/event-stream"}
            with self.subTest(failure=type(failure).__name__), patch("messaging_llm_bot.http.urlopen", return_value=response):
                with self.assertRaises(StreamError) as caught:
                    list(HttpClient(1).post_json_stream("https://user:password@example.invalid?key=PRIVATE", {}))
            self.assertIn("Interrupted event stream", str(caught.exception))
            rendered = "".join(traceback.format_exception(caught.exception))
            for secret in ("PRIVATE", "password", "user:"):
                self.assertNotIn(secret, rendered)
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
