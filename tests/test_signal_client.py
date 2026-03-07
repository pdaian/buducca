import unittest
from unittest.mock import patch

from telegram_llm_bot.signal_client import SignalClient, SignalFrontendUnavailableError


class SignalClientTests(unittest.TestCase):
    def test_parses_text_and_voice_messages(self) -> None:
        stdout = "\n".join(
            [
                '{"envelope":{"source":"+15550001","dataMessage":{"message":"hello"}}}',
                '{"envelope":{"source":"+15550002","dataMessage":{"attachments":[{"contentType":"audio/ogg","storedFilename":"voice.ogg"}]}}}',
            ]
        )

        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stdout
            run.return_value.stderr = ""
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000")
                updates = client.get_updates()

        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0].text, "hello")
        self.assertEqual(updates[1].voice_file_path.endswith("voice.ogg"), True)


    def test_parses_sync_sent_message_for_note_to_self(self) -> None:
        stdout = (
            '{"envelope":{"source":"+15551230000","syncMessage":{"sentMessage":{"destination":"+15551230000","message":"remember milk"}}}}'
        )

        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stdout
            run.return_value.stderr = ""
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000")
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].conversation_id, "+15551230000")
        self.assertEqual(updates[0].sender_id, "+15551230000")
        self.assertEqual(updates[0].text, "remember milk")

    def test_parses_sync_sent_message_with_audio_attachment(self) -> None:
        stdout = (
            '{"envelope":{"source":"+15551230000","syncMessage":{"sentMessage":{"destination":"+15559998888","attachments":[{"contentType":"audio/ogg","filename":"sync.ogg"}]}}}}'
        )

        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stdout
            run.return_value.stderr = ""
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000")
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].conversation_id, "+15559998888")
        self.assertEqual(updates[0].sender_id, "+15551230000")
        self.assertTrue(updates[0].voice_file_path.endswith("sync.ogg"))

    def test_parses_note_to_self_audio_when_content_type_is_missing(self) -> None:
        stdout = (
            '{"envelope":{"source":"+15551230000","syncMessage":{"sentMessage":{"attachments":[{"filename":"voice-note.m4a"}]}}}}'
        )

        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stdout
            run.return_value.stderr = ""
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000")
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].conversation_id, "+15551230000")
        self.assertEqual(updates[0].sender_id, "+15551230000")
        self.assertTrue(updates[0].voice_file_path.endswith("voice-note.m4a"))

    def test_parses_sync_voice_note_with_number_uuid_fields(self) -> None:
        stdout = (
            '{"envelope":{"sourceUuid":"user-uuid","syncMessage":{"sentMessage":{"destinationNumber":"+15551230000","attachments":[{"voiceNote":true,"storedFilename":"memo.ogg"}]}}}}'
        )

        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stdout
            run.return_value.stderr = ""
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000")
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].conversation_id, "+15551230000")
        self.assertEqual(updates[0].sender_id, "user-uuid")
        self.assertTrue(updates[0].voice_file_path.endswith("memo.ogg"))


    def test_send_message_supports_note_to_self_recipient(self) -> None:
        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            client = SignalClient(account="+15551230000")
            client.send_message("+15551230000", "note to self")

        run.assert_called_once_with(
            ["signal-cli", "-a", "+15551230000", "send", "-m", "note to self", "+15551230000"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_raises_when_signal_cli_missing(self) -> None:
        client = SignalClient(account="+15551230000")

        with patch("telegram_llm_bot.signal_client.which", return_value=None):
            with self.assertRaises(SignalFrontendUnavailableError):
                client.get_updates()


    def test_registration_error_is_treated_as_frontend_unavailable(self) -> None:
        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            run.return_value.stderr = "User +15551230000 is not registered."
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000")
                with self.assertRaises(SignalFrontendUnavailableError) as exc:
                    client.get_updates()

        self.assertIn("signal_signup", str(exc.exception))

    def test_raises_when_json_output_not_configured(self) -> None:
        client = SignalClient(account="+15551230000", receive_command=["signal-cli", "-a", "+15551230000", "receive"])

        with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
            with self.assertRaises(SignalFrontendUnavailableError):
                client.get_updates()


if __name__ == "__main__":
    unittest.main()
