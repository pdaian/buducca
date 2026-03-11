import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from messaging_llm_bot.whatsapp_client import WhatsAppClient, WhatsAppFrontendUnavailableError, main


class WhatsAppClientTests(unittest.TestCase):
    def test_get_updates_parses_list_payload(self) -> None:
        payload = '[{"conversation_id":"group:Family|g1","sender_id":"+15550001","text":"hello","sender_name":"Alice","attachments":[{"path":"/tmp/a.pdf","name":"a.pdf","mime_type":"application/pdf"}]}]'
        with patch("messaging_llm_bot.whatsapp_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.whatsapp_client.which", return_value="/usr/bin/python3"):
                client = WhatsAppClient(receive_command=["python3", "recv.py"], send_command=["python3", "send.py", "{recipient}", "{message}"])
                updates = client.get_updates()
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].backend, "whatsapp")
        self.assertEqual(updates[0].conversation_id, "group:Family|g1")
        self.assertEqual(updates[0].sender_contact, "Alice")
        self.assertEqual(updates[0].attachments[0].filename, "a.pdf")

    def test_get_updates_accepts_attachment_only_message(self) -> None:
        payload = '[{"conversation_id":"chat-1","sender_id":"+15550001","attachments":[{"path":"/tmp/a.pdf","name":"a.pdf"}]}]'
        with patch("messaging_llm_bot.whatsapp_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.whatsapp_client.which", return_value="/usr/bin/python3"):
                client = WhatsAppClient(receive_command=["python3", "recv.py"], send_command=["python3", "send.py", "{recipient}", "{message}"])
                updates = client.get_updates()
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].attachments[0].file_path, "/tmp/a.pdf")

    def test_send_message_replaces_placeholders(self) -> None:
        with patch("messaging_llm_bot.whatsapp_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout="", stderr="")
            with patch("messaging_llm_bot.whatsapp_client.which", return_value="/usr/bin/python3"):
                client = WhatsAppClient(receive_command=["python3", "recv.py"], send_command=["python3", "send.py", "--to", "{recipient}", "--message", "{message}"])
                client.send_message("group:Family|g1", "hi")
        run.assert_called_once_with(
            ["python3", "send.py", "--to", "group:Family|g1", "--message", "hi"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_send_message_strips_attachment_placeholder_args(self) -> None:
        with patch("messaging_llm_bot.whatsapp_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout="", stderr="")
            with patch("messaging_llm_bot.whatsapp_client.which", return_value="/usr/bin/python3"):
                client = WhatsAppClient(
                    receive_command=["python3", "recv.py"],
                    send_command=["python3", "send.py", "--recipient", "{recipient}", "--message", "{message}", "--attachment", "{attachment}"],
                )
                client.send_message("group:Family|g1", "hi")
        run.assert_called_once_with(
            ["python3", "send.py", "--recipient", "group:Family|g1", "--message", "hi"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_missing_executable_raises_frontend_unavailable(self) -> None:
        client = WhatsAppClient(receive_command=["missingcmd", "recv.py"], send_command=["python3", "send.py"])
        with patch("messaging_llm_bot.whatsapp_client.which", return_value=None):
            with self.assertRaises(WhatsAppFrontendUnavailableError):
                client.get_updates()

    def test_normalize_command_paths_rewrites_legacy_repo_root(self) -> None:
        client = WhatsAppClient(receive_command=["python3", "/home/ai/buducca/scripts/whatsapp_receive.py"], send_command=[])
        with TemporaryDirectory() as td:
            repo_root = Path(td)
            script_path = repo_root / "scripts" / "whatsapp_receive.py"
            script_path.parent.mkdir(parents=True)
            script_path.write_text("print('ok')", encoding="utf-8")
            client._repo_root = repo_root

            normalized = client._normalize_command_paths(client.receive_command)

        self.assertEqual(normalized, ["python3", str(script_path)])

    def test_get_updates_missing_python_script_raises_frontend_unavailable(self) -> None:
        client = WhatsAppClient(
            receive_command=["python3", "/tmp/missing_whatsapp_receive.py"],
            send_command=["python3", "send.py"],
        )
        with patch("messaging_llm_bot.whatsapp_client.which", return_value="/usr/bin/python3"):
            with self.assertRaises(WhatsAppFrontendUnavailableError):
                client.get_updates()

    def test_main_receive_outputs_json_messages(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["receive", "--account", "personal"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue().strip(), '{"messages": []}')


if __name__ == "__main__":
    unittest.main()
