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

    def test_raises_when_signal_cli_missing(self) -> None:
        client = SignalClient(account="+15551230000")

        with patch("telegram_llm_bot.signal_client.which", return_value=None):
            with self.assertRaises(SignalFrontendUnavailableError):
                client.get_updates()

    def test_raises_when_json_output_not_configured(self) -> None:
        client = SignalClient(account="+15551230000", receive_command=["signal-cli", "-a", "+15551230000", "receive"])

        with patch("telegram_llm_bot.signal_client.which", return_value="/usr/bin/signal-cli"):
            with self.assertRaises(SignalFrontendUnavailableError):
                client.get_updates()


if __name__ == "__main__":
    unittest.main()
