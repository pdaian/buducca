from pathlib import Path
import types
import io
import unittest
from unittest.mock import Mock, patch

from messaging_llm_bot.google_fi_client import GoogleFiClient, GoogleFiFrontendUnavailableError


class GoogleFiClientTests(unittest.TestCase):
    def test_get_updates_parses_messages_and_calls(self) -> None:
        payload = '{"messages":[{"conversation_id":"thread-1","sender_id":"+15550001","text":"hello"}],"calls":[{"conversation_id":"thread-1","sender_id":"+15550001","status":"missed"}]}'
        with patch("messaging_llm_bot.google_fi_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.google_fi_client.which", return_value="/usr/bin/python3"):
                client = GoogleFiClient(receive_command=["python3", "recv.py"], send_command=["python3", "send.py", "{recipient}", "{message}"])
                updates = client.get_updates()
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0].backend, "google_fi")
        self.assertEqual(updates[1].event_type, "call")

    def test_send_message_replaces_placeholders(self) -> None:
        with patch("messaging_llm_bot.google_fi_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout="", stderr="")
            with patch("messaging_llm_bot.google_fi_client.which", return_value="/usr/bin/python3"):
                client = GoogleFiClient(receive_command=["python3", "recv.py"], send_command=["python3", "send.py", "--to", "{recipient}", "--message", "{message}"])
                client.send_message("thread-1", "hi")
        run.assert_called_once()

    def test_missing_executable_raises_frontend_unavailable(self) -> None:
        client = GoogleFiClient(receive_command=["missingcmd", "recv.py"], send_command=["python3", "send.py"])
        with patch("messaging_llm_bot.google_fi_client.which", return_value=None):
            with self.assertRaises(GoogleFiFrontendUnavailableError):
                client.get_updates()


class GoogleFiCliErrorHandlingTests(unittest.TestCase):
    def test_open_messages_page_reports_playwright_mismatch(self) -> None:
        fake_sync_api = types.ModuleType("playwright.sync_api")
        sync_pw = Mock()
        sync_pw.return_value.start.side_effect = KeyError("deviceDescriptors")
        fake_sync_api.sync_playwright = sync_pw

        with patch.dict("sys.modules", {"playwright": types.ModuleType("playwright"), "playwright.sync_api": fake_sync_api}):
            from messaging_llm_bot.google_fi_client import BrowserOptions, _open_messages_page

            with self.assertRaises(GoogleFiFrontendUnavailableError) as ctx:
                _open_messages_page(BrowserOptions(workspace=Path("workspace")))
        self.assertIn("deviceDescriptors", str(ctx.exception))

    def test_main_returns_nonzero_for_frontend_unavailable(self) -> None:
        from messaging_llm_bot import google_fi_client

        with patch.object(google_fi_client, "receive_events", side_effect=GoogleFiFrontendUnavailableError("broken")):
            with patch("sys.stderr", new_callable=io.StringIO):
                code = google_fi_client.main(["receive"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
