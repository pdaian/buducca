import subprocess
import unittest
from unittest.mock import Mock, patch

from messaging_llm_bot.bot import BotRunner
from messaging_llm_bot.signal_client import SignalClient, SignalFrontendUnavailableError


class SignalRecoveryTests(unittest.TestCase):
    """Use the real send path with subprocesses and persistence kept in memory."""

    def setUp(self) -> None:
        self.bot = BotRunner.__new__(BotRunner)
        self.bot.signal = SignalClient(account="+15551230000")
        self.bot._signal_frontend_disabled = False
        self.bot._set_frontend_disabled = Mock()
        self.bot._append_frontend_log = Mock()

    def test_unregistered_sender_disables_frontend_after_one_attempt(self) -> None:
        result = subprocess.CompletedProcess([], 1, "", "User +15551230000 is not registered.")
        with patch("messaging_llm_bot.signal_client.subprocess.run", return_value=result) as run:
            for _ in range(2):
                with self.assertRaises(SignalFrontendUnavailableError):
                    self.bot._send_signal_message("+15559999999", "a long response that must not be split and retried")
            run.assert_called_once()
        self.assertTrue(self.bot._signal_frontend_disabled)
        self.bot._set_frontend_disabled.assert_called_once()
        self.bot._append_frontend_log.assert_not_called()
        self.assertIn("repair_signal.py", self.bot._set_frontend_disabled.call_args.kwargs["error"])

    def test_other_send_failures_are_not_retried_or_logged_as_success(self) -> None:
        for detail in ("Recipient +15559999999 is not registered", "The user +15559999999 is not registered.",
                       "Unregistered user", "Rate limit exceeded",
                       "Untrusted identity", "Connection reset", "HTTP 500"):
            with self.subTest(detail=detail), patch("messaging_llm_bot.signal_client.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", detail)) as run:
                with self.assertRaises(RuntimeError) as error:
                    self.bot._send_signal_message("+15559999999", "a long message which must not be retried")
                self.assertNotIsInstance(error.exception, SignalFrontendUnavailableError)
                run.assert_called_once()
        self.assertFalse(self.bot._signal_frontend_disabled)
        self.bot._append_frontend_log.assert_not_called()

    def test_timeout_is_not_retried_because_delivery_is_unknown(self) -> None:
        with patch("messaging_llm_bot.signal_client.subprocess.run", side_effect=subprocess.TimeoutExpired("send", 30)) as run:
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.bot._send_signal_message("+15559999999", "may have been delivered already")
            run.assert_called_once()
        self.assertFalse(self.bot._signal_frontend_disabled)

    def test_size_rejection_still_splits_and_logs_only_accepted_chunks(self) -> None:
        def send(command, **_kwargs):
            message = command[command.index("-m") + 1]
            return subprocess.CompletedProcess(command, 1 if len(message) > 5 else 0, "", "message too long" if len(message) > 5 else "")

        with patch("messaging_llm_bot.signal_client.subprocess.run", side_effect=send) as run:
            self.bot._send_signal_message("+15559999999", "hello world")
        self.assertEqual(run.call_count, 3)
        self.assertEqual([call.kwargs["text"] for call in self.bot._append_frontend_log.call_args_list], ["hello", "world"])

    def test_group_send_preserves_custom_binary_account_directory_and_long_message_flag(self) -> None:
        client = SignalClient(account="+15551230000", send_command=[
            "/opt/signal-cli", "--config", "/srv/signal", "-a", "+15551230000", "send", "--message", "{message}", "{recipient}",
        ])
        with patch("messaging_llm_bot.signal_client.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            client.send_message("group:Family|group-id", "hello")
        self.assertEqual(run.call_args.args[0], [
            "/opt/signal-cli", "--config", "/srv/signal", "-a", "+15551230000", "send", "-m", "hello", "-g", "group-id",
        ])

    def test_attachment_registration_failure_has_recovery_instructions(self) -> None:
        result = subprocess.CompletedProcess([], 1, "", "User +15551230000 is not registered.")
        with patch("messaging_llm_bot.signal_client.Path.exists", return_value=True), patch("messaging_llm_bot.signal_client.subprocess.run", return_value=result):
            with self.assertRaisesRegex(SignalFrontendUnavailableError, "repair_signal.py"):
                self.bot.signal.send_file("+15559999999", "/virtual/attachment.pdf")

    def test_missing_send_executable_is_reported_as_frontend_unavailable(self) -> None:
        with patch("messaging_llm_bot.signal_client.subprocess.run", side_effect=FileNotFoundError("signal-cli")):
            with self.assertRaisesRegex(SignalFrontendUnavailableError, "repair_signal.py"):
                self.bot._send_signal_message("+15559999999", "hello")
        self.assertTrue(self.bot._signal_frontend_disabled)


if __name__ == "__main__":
    unittest.main()
