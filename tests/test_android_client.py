import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from messaging_llm_bot.android_client import AndroidClient, AndroidFrontendUnavailableError, main
from messaging_llm_bot.termux_notification_collector import collect_once


class AndroidClientTests(unittest.TestCase):
    def test_get_updates_parses_sms_and_notification_payloads(self) -> None:
        payload = json.dumps(
            {
                "messages": [
                    {
                        "type": "sms",
                        "sender_id": "+15550001",
                        "body": "hello",
                    },
                    {
                        "type": "notification",
                        "package_name": "org.example.app",
                        "title": "Build",
                        "body": "Finished",
                    },
                ]
            }
        )
        with patch("messaging_llm_bot.android_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout=payload, stderr="")
            with patch("messaging_llm_bot.android_client.which", return_value="/usr/bin/python3"):
                client = AndroidClient(
                    receive_command=["python3", "recv.py"],
                    send_command=["python3", "send.py", "{recipient}", "{message}"],
                )
                updates = client.get_updates()

        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0].backend, "android")
        self.assertEqual(updates[0].conversation_id, "+15550001")
        self.assertEqual(updates[1].sender_id, "notif:org.example.app")
        self.assertIn("[Notification]", updates[1].text or "")

    def test_send_message_replaces_placeholders(self) -> None:
        with patch("messaging_llm_bot.android_client.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout="", stderr="")
            with patch("messaging_llm_bot.android_client.which", return_value="/usr/bin/python3"):
                client = AndroidClient(
                    receive_command=["python3", "recv.py"],
                    send_command=["python3", "send.py", "--to", "{recipient}", "--message", "{message}"],
                )
                client.send_message("+15550001", "hi")

        run.assert_called_once_with(
            ["python3", "send.py", "--to", "+15550001", "--message", "hi"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_missing_executable_raises_frontend_unavailable(self) -> None:
        client = AndroidClient(receive_command=["missingcmd"], send_command=["python3", "send.py"])
        with patch("messaging_llm_bot.android_client.which", return_value=None):
            with self.assertRaises(AndroidFrontendUnavailableError):
                client.get_updates()

    def test_main_receive_reads_only_new_jsonl_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "android-state.json"
            inbox.write_text(
                json.dumps({"type": "sms", "sender_id": "+15550001", "body": "hello"}) + "\n",
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["receive", "--inbox", str(inbox), "--state-file", str(state)])

            self.assertEqual(exit_code, 0)
            self.assertIn("+15550001", stdout.getvalue())

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["receive", "--inbox", str(inbox), "--state-file", str(state)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue().strip(), '{"messages": []}')

    def test_termux_notification_collector_appends_only_new_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "termux-state.json"
            payload = json.dumps(
                [
                    {
                        "packageName": "org.example.chat",
                        "applicationLabel": "Example Chat",
                        "id": 41,
                        "title": "Alice",
                        "content": "hello",
                        "when": "2026-03-18T09:01:00-04:00",
                    }
                ]
            )

            with patch("messaging_llm_bot.termux_notification_collector.which", return_value="/usr/bin/termux-notification-list"):
                with patch("messaging_llm_bot.termux_notification_collector.subprocess.run") as run:
                    run.return_value = Mock(returncode=0, stdout=payload, stderr="")
                    appended = collect_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                    )

                    self.assertEqual(appended, 1)
                    written = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()]
                    self.assertEqual(
                        written,
                        [
                            {
                                "type": "notification",
                                "package_name": "org.example.chat",
                                "app_name": "Example Chat",
                                "title": "Alice",
                                "body": "hello",
                                "timestamp": "2026-03-18T09:01:00-04:00",
                            }
                        ],
                    )

                    appended = collect_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                    )
                    self.assertEqual(appended, 0)
                    run.assert_called_with(
                        ["termux-notification-list"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

    def test_termux_notification_collector_filters_packages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "android-events.jsonl"
            state = Path(td) / "termux-state.json"
            payload = json.dumps(
                [
                    {"packageName": "org.keep", "title": "Wanted", "content": "collect"},
                    {"packageName": "org.drop", "title": "Noise", "content": "ignore"},
                ]
            )

            with patch("messaging_llm_bot.termux_notification_collector.which", return_value="/usr/bin/termux-notification-list"):
                with patch("messaging_llm_bot.termux_notification_collector.subprocess.run") as run:
                    run.return_value = Mock(returncode=0, stdout=payload, stderr="")
                    appended = collect_once(
                        inbox_path=inbox,
                        state_path=state,
                        notification_command="termux-notification-list",
                        include_packages={"org.keep"},
                    )

            self.assertEqual(appended, 1)
            written = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0]["package_name"], "org.keep")


if __name__ == "__main__":
    unittest.main()
