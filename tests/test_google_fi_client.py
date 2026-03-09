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
        self.assertEqual(updates[1].text, "[Call event] missed")

    def test_send_message_replaces_placeholders(self) -> None:
        with patch("messaging_llm_bot.google_fi_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout="", stderr="")
            with patch("messaging_llm_bot.google_fi_client.which", return_value="/usr/bin/python3"):
                client = GoogleFiClient(receive_command=["python3", "recv.py"], send_command=["python3", "send.py", "--to", "{recipient}", "--message", "{message}"])
                client.send_message("thread-1", "hi")
        run.assert_called_once_with(
            ["python3", "send.py", "--to", "thread-1", "--message", "hi"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_missing_executable_raises_frontend_unavailable(self) -> None:
        client = GoogleFiClient(receive_command=["missingcmd", "recv.py"], send_command=["python3", "send.py"])
        with patch("messaging_llm_bot.google_fi_client.which", return_value=None):
            with self.assertRaises(GoogleFiFrontendUnavailableError):
                client.get_updates()


if __name__ == "__main__":
    unittest.main()
