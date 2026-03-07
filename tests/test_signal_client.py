import unittest
from unittest.mock import Mock, patch

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

    def test_parses_group_data_message_using_group_conversation_id(self) -> None:
        stdout = (
            '{"envelope":{"source":"+15550001","dataMessage":{"groupInfo":{"groupId":"group-123","title":"Family Chat"},"message":"hi group"}}}'
        )

        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stdout
            run.return_value.stderr = ""
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000")
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].conversation_id, "group:Family Chat|group-123")
        self.assertEqual(updates[0].sender_id, "+15550001")
        self.assertEqual(updates[0].text, "hi group")

    def test_parses_sync_sent_group_message_using_group_conversation_id(self) -> None:
        stdout = (
            '{"envelope":{"source":"+15551230000","syncMessage":{"sentMessage":{"groupInfo":{"groupId":"group-123","title":"Family Chat"},"message":"group note"}}}}'
        )

        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = stdout
            run.return_value.stderr = ""
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000")
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].conversation_id, "group:Family Chat|group-123")
        self.assertEqual(updates[0].sender_id, "+15551230000")
        self.assertEqual(updates[0].text, "group note")



    def test_default_contacts_command_uses_json_output_flag(self) -> None:
        message_stdout = '{"envelope":{"source":"+15550001","dataMessage":{"message":"hello"}}}'

        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.side_effect = [
                Mock(returncode=0, stdout='[]', stderr=""),
                Mock(returncode=0, stdout=message_stdout, stderr=""),
            ]
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000")
                client.get_updates()

        self.assertEqual(run.call_args_list[0].args[0], ["signal-cli", "-o", "json", "-a", "+15551230000", "listContacts"])


    def test_uses_cached_contact_name_when_message_lacks_name(self) -> None:
        contacts_stdout = '[{"number":"+15550001","name":"Alice"}]'
        message_stdout = '{"envelope":{"source":"+15550001","dataMessage":{"message":"hello"}}}'

        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.side_effect = [
                Mock(returncode=0, stdout=contacts_stdout, stderr=""),
                Mock(returncode=0, stdout=message_stdout, stderr=""),
            ]
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000")
                updates = client.get_updates()

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].sender_name, "Alice")

    def test_refreshes_contact_cache_after_ttl(self) -> None:
        contacts_first = '[{"number":"+15550001","name":"Alice"}]'
        contacts_second = '[{"number":"+15550001","name":"Alicia"}]'
        message_stdout = '{"envelope":{"source":"+15550001","dataMessage":{"message":"hello"}}}'

        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.side_effect = [
                Mock(returncode=0, stdout=contacts_first, stderr=""),
                Mock(returncode=0, stdout=message_stdout, stderr=""),
                Mock(returncode=0, stdout=contacts_second, stderr=""),
                Mock(returncode=0, stdout=message_stdout, stderr=""),
            ]
            with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
                client = SignalClient(account="+15551230000", contacts_cache_ttl_seconds=10)
                with patch("telegram_llm_bot.signal_client.time.monotonic", side_effect=[0.0, 20.0]):
                    first = client.get_updates()
                    second = client.get_updates()

        self.assertEqual(first[0].sender_name, "Alice")
        self.assertEqual(second[0].sender_name, "Alicia")

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

    def test_send_message_supports_group_conversation_id(self) -> None:
        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            client = SignalClient(account="+15551230000")
            client.send_message("group:Family Chat|group-123", "hello group")

        run.assert_called_once_with(
            ["signal-cli", "-a", "+15551230000", "send", "-m", "hello group", "-g", "group-123"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_send_message_still_supports_raw_group_conversation_id(self) -> None:
        with patch("telegram_llm_bot.signal_client.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            client = SignalClient(account="+15551230000")
            client.send_message("group:group-123", "hello group")

        run.assert_called_once_with(
            ["signal-cli", "-a", "+15551230000", "send", "-m", "hello group", "-g", "group-123"],
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
