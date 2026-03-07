import json
import tempfile
import unittest
from unittest.mock import patch

from assistant_framework.cli import _warn_signal_attachment_mismatch
from assistant_framework.workspace import Workspace
from collectors.signal_messages import create_collector


class SignalCollectorCollisionTests(unittest.TestCase):
    def test_signal_collector_default_command_uses_ignore_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            collector = create_collector(
                {
                    "device_name": "+15550001111",
                    "account_name": "primary",
                }
            )

            called_commands = []

            def fake_run(command, timeout_seconds=60.0, cwd=None):
                called_commands.append(command)
                payload = {
                    "envelope": {
                        "timestamp": 100,
                        "source": "+15559990000",
                        "dataMessage": {"message": "hello"},
                    }
                }
                return 0, json.dumps(payload), ""

            with patch("collectors.signal_messages.run_command", fake_run):
                collector["run"](workspace)

            self.assertIn("--ignore-attachments", called_commands[0])

    def test_signal_collector_can_disable_ignore_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            collector = create_collector(
                {
                    "device_name": "+15550001111",
                    "account_name": "primary",
                    "ignore_attachments": False,
                }
            )

            called_commands = []

            def fake_run(command, timeout_seconds=60.0, cwd=None):
                called_commands.append(command)
                return 0, "", ""

            with patch("collectors.signal_messages.run_command", fake_run):
                collector["run"](workspace)

            self.assertNotIn("--ignore-attachments", called_commands[0])

    def test_merges_frontend_history_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text(
                "logs/signal.history",
                json.dumps(
                    {
                        "logged_at": "2024-01-01T00:00:00+00:00",
                        "backend": "signal",
                        "direction": "incoming",
                        "conversation_id": "+15559990000",
                        "sender_id": "+15559990000",
                        "text": "hi from signal frontend",
                    }
                )
                + "\n",
            )
            collector = create_collector({"device_name": "+15550001111"})

            called_commands = []

            def fake_run(command, timeout_seconds=60.0, cwd=None):
                called_commands.append(command)
                return 0, "", ""

            with patch("collectors.signal_messages.run_command", fake_run):
                collector["run"](workspace)

            output = workspace.read_text("signal.messages.recent")
            self.assertIn('"source": "frontend_log"', output)
            self.assertEqual(called_commands, [["signal-cli", "-o", "json", "-a", "+15550001111", "receive", "--ignore-attachments"]])

    def test_warns_on_shared_account_collision_when_attachments_ignored(self) -> None:
        collector_config = {
            "signal_messages": {
                "accounts": [
                    {
                        "name": "primary",
                        "device_name": "+15551234567",
                        "ignore_attachments": True,
                    }
                ]
            }
        }
        bot_config = {"signal": {"account": "+15551234567"}}

        with patch("assistant_framework.cli.logging.warning") as warning:
            _warn_signal_attachment_mismatch(collector_config, bot_config)

        warning.assert_called_once()
        self.assertIn("same account", warning.call_args.args[0])

    def test_does_not_warn_when_overlap_preserves_attachments(self) -> None:
        collector_config = {
            "signal_messages": {
                "accounts": [
                    {
                        "name": "primary",
                        "device_name": "+15551234567",
                        "ignore_attachments": False,
                    }
                ]
            }
        }
        bot_config = {"signal": {"account": "+15551234567"}}

        with patch("assistant_framework.cli.logging.warning") as warning:
            _warn_signal_attachment_mismatch(collector_config, bot_config)

        warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
