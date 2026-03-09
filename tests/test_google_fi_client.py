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

    def test_get_updates_parses_numeric_and_nested_fields(self) -> None:
        payload = '{"messages":[{"thread_id":12345,"number":15550001,"content":{"text":"hello"}}]}'
        with patch("messaging_llm_bot.google_fi_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.google_fi_client.which", return_value="/usr/bin/python3"):
                client = GoogleFiClient(receive_command=["python3", "recv.py"], send_command=["python3", "send.py", "{recipient}", "{message}"])
                updates = client.get_updates()
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].conversation_id, "12345")
        self.assertEqual(updates[0].sender_id, "15550001")
        self.assertEqual(updates[0].text, "hello")

    def test_get_updates_normalizes_phone_from_sender_id(self) -> None:
        payload = '{"messages":[{"conversation_id":"thread-1","sender_id":"(555) 111-2222","text":"hello"}]}'
        with patch("messaging_llm_bot.google_fi_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.google_fi_client.which", return_value="/usr/bin/python3"):
                client = GoogleFiClient(receive_command=["python3", "recv.py"], send_command=["python3", "send.py", "{recipient}", "{message}"])
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].sender_id, "5551112222")

    def test_get_updates_uses_sender_contact_when_sender_id_is_name(self) -> None:
        payload = '{"messages":[{"conversation_id":"thread-1","sender_id":"Mom","sender_contact":"+1 (555) 111-2222","text":"hello"}]}'
        with patch("messaging_llm_bot.google_fi_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.google_fi_client.which", return_value="/usr/bin/python3"):
                client = GoogleFiClient(receive_command=["python3", "recv.py"], send_command=["python3", "send.py", "{recipient}", "{message}"])
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].sender_id, "+15551112222")


class GoogleFiCliErrorHandlingTests(unittest.TestCase):
    def test_ensure_logged_in_waits_for_signup_in_headful_mode(self) -> None:
        from messaging_llm_bot import google_fi_client

        page = Mock()
        page.wait_for_selector.side_effect = [Exception("not yet"), None]

        google_fi_client._ensure_logged_in(page, 15000, headful=True, signup_wait_ms=60000)

        self.assertEqual(page.wait_for_selector.call_count, 2)
        first_call = page.wait_for_selector.call_args_list[0]
        second_call = page.wait_for_selector.call_args_list[1]
        self.assertEqual(first_call.kwargs["timeout"], 15000)
        self.assertEqual(second_call.kwargs["timeout"], 60000)

    def test_ensure_logged_in_raises_without_headful_signup_wait(self) -> None:
        from messaging_llm_bot import google_fi_client

        page = Mock()
        page.wait_for_selector.side_effect = Exception("not logged in")

        with self.assertRaises(google_fi_client.GoogleFiAutomationError):
            google_fi_client._ensure_logged_in(page, 15000, headful=False, signup_wait_ms=60000)

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

    def test_open_messages_page_falls_back_when_channel_unavailable(self) -> None:
        from messaging_llm_bot.google_fi_client import BrowserOptions, _open_messages_page

        context = Mock()
        context.pages = [Mock()]

        launch = Mock(side_effect=[Exception("Unsupported channel"), context])
        chromium = Mock(launch_persistent_context=launch)
        pw = Mock(chromium=chromium)
        sync_pw = Mock()
        sync_pw.return_value.start.return_value = pw
        fake_sync_api = types.ModuleType("playwright.sync_api")
        fake_sync_api.sync_playwright = sync_pw

        with patch.dict("sys.modules", {"playwright": types.ModuleType("playwright"), "playwright.sync_api": fake_sync_api}):
            p, opened_context, page = _open_messages_page(BrowserOptions(workspace=Path("workspace")))

        self.assertIs(p, pw)
        self.assertIs(opened_context, context)
        self.assertIs(page, context.pages[0])
        self.assertEqual(launch.call_count, 2)
        first_kwargs = launch.call_args_list[0].kwargs
        second_kwargs = launch.call_args_list[1].kwargs
        self.assertEqual(first_kwargs["channel"], "chrome")
        self.assertNotIn("channel", second_kwargs)

    def test_ensure_logged_in_reports_google_secure_browser_block(self) -> None:
        from messaging_llm_bot import google_fi_client

        page = Mock()
        page.wait_for_selector.side_effect = Exception("not logged in")
        secure_banner = Mock()
        secure_banner.count.return_value = 1
        page.locator.return_value = secure_banner

        with self.assertRaises(google_fi_client.GoogleFiAutomationError) as ctx:
            google_fi_client._ensure_logged_in(page, 15000, headful=False, signup_wait_ms=0)
        self.assertIn("insecure browser", str(ctx.exception).lower())

    def test_main_returns_nonzero_for_frontend_unavailable(self) -> None:
        from messaging_llm_bot import google_fi_client

        with patch.object(google_fi_client, "receive_events", side_effect=GoogleFiFrontendUnavailableError("broken")):
            with patch("sys.stderr", new_callable=io.StringIO):
                code = google_fi_client.main(["receive"])
        self.assertEqual(code, 2)


class GoogleFiConversationParsingTests(unittest.TestCase):
    def test_extract_conversation_id_from_row_ignores_new_conversation(self) -> None:
        from messaging_llm_bot.google_fi_client import _extract_conversation_id_from_row

        row = Mock()
        row.get_attribute.side_effect = lambda name: '/web/conversations/new' if name == 'href' else None

        self.assertIsNone(_extract_conversation_id_from_row(row, 0))

    def test_extract_conversation_id_from_row_uses_nested_anchor(self) -> None:
        from messaging_llm_bot.google_fi_client import _extract_conversation_id_from_row

        row = Mock()
        row.get_attribute.return_value = None
        nested_first = Mock()
        nested_first.get_attribute.return_value = '/web/conversations/12345'
        nested = Mock()
        nested.count.return_value = 1
        nested.first = nested_first
        row.locator.return_value = nested

        self.assertEqual(_extract_conversation_id_from_row(row, 0), '12345')

    def test_find_conversation_rows_prefers_first_non_empty_selector(self) -> None:
        from messaging_llm_bot.google_fi_client import _find_conversation_rows

        page = Mock()
        empty = Mock()
        empty.count.return_value = 0
        populated = Mock()
        populated.count.return_value = 3

        def locator_side_effect(selector: str):
            if selector == "[data-e2e-conversation-id]":
                return populated
            return empty

        page.locator.side_effect = locator_side_effect

        rows, selector = _find_conversation_rows(page)

        self.assertIs(rows, populated)
        self.assertEqual(selector, "[data-e2e-conversation-id]")

    def test_find_message_bubbles_prefers_first_non_empty_selector(self) -> None:
        from messaging_llm_bot.google_fi_client import _find_message_bubbles

        page = Mock()
        empty = Mock()
        empty.count.return_value = 0
        populated = Mock()
        populated.count.return_value = 2

        def locator_side_effect(selector: str):
            if selector == "mws-message-text-content":
                return populated
            return empty

        page.locator.side_effect = locator_side_effect

        bubbles, selector = _find_message_bubbles(page)

        self.assertIs(bubbles, populated)
        self.assertEqual(selector, "mws-message-text-content")


if __name__ == "__main__":
    unittest.main()
