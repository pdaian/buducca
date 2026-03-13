import importlib.util
import tempfile
import unittest
from email.message import Message
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_fetch_url_module():
    skill_path = Path("skills/fetch_url/__init__.py")
    spec = importlib.util.spec_from_file_location("fetch_url_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load fetch_url skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str = "https://example.com/final",
        status: int | None = 200,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self._body = body
        self._url = url
        self.status = status
        self.headers = Message()
        if content_type:
            self.headers["Content-Type"] = content_type

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self._body
        return self._body[:size]

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int | None:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class FetchUrlSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_fetch_url_module()

    def test_missing_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {})
            self.assertEqual(result, "Missing required arg `url`.")

    def test_requires_scheme(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"url": "example.com"})
            self.assertEqual(result, "Invalid arg `url`. A URL scheme is required.")

    def test_fetches_data_url_as_text(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"url": "data:text/plain,hello%20world"})
            self.assertIn("URL: data:text/plain,hello%20world", result)
            self.assertIn("Content-Type: text/plain", result)
            self.assertTrue(result.endswith("\n\nhello world"))

    def test_fetches_file_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            file_path = Path(td) / "sample.html"
            file_path.write_text("<html><body>local file</body></html>", encoding="utf-8")

            result = self.module.run(workspace, {"url": file_path.resolve().as_uri()})

            self.assertIn("Content-Type: text/html", result)
            self.assertIn("<html><body>local file</body></html>", result)

    def test_http_requests_use_request_with_user_agent(self) -> None:
        observed: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            observed["request"] = request
            observed["timeout"] = timeout
            return _FakeResponse(b"<html><body>ok</body></html>")

        self.module.urlopen = fake_urlopen

        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"url": "https://example.com", "timeout_seconds": 7})

        request = observed["request"]
        self.assertEqual(observed["timeout"], 7)
        self.assertEqual(request.full_url, "https://example.com")
        self.assertIn("buducca-fetch-url-skill/1.0", request.headers["User-agent"])
        self.assertIn("<html><body>ok</body></html>", result)

    def test_binary_payloads_are_base64_encoded(self) -> None:
        self.module.urlopen = lambda request, timeout: _FakeResponse(
            b"\x00\x01\x02\x03",
            content_type="application/octet-stream",
        )

        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"url": "https://example.com/blob"})

        self.assertIn("Content-Type: application/octet-stream", result)
        self.assertIn("Content-Transfer-Encoding: base64", result)
        self.assertTrue(result.endswith("\n\nAAECAw=="))

    def test_respects_max_bytes_limit(self) -> None:
        self.module.urlopen = lambda request, timeout: _FakeResponse(
            b"abcdefghijklmnopqrstuvwxyz",
            content_type="text/plain; charset=utf-8",
        )

        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"url": "https://example.com/large", "max_bytes": 5})

        self.assertIn("Truncated: yes", result)
        self.assertTrue(result.endswith("\n\nabcde"))


if __name__ == "__main__":
    unittest.main()
