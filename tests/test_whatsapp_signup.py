import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from messaging_llm_bot.whatsapp_signup import run_signup


class WhatsAppSignupTests(unittest.TestCase):
    def test_prints_whatsapp_qr_setup_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td) / "config"
            config_dir.mkdir()
            (config_dir / "whatsapp.json").write_text(
                """
                {
                  "receive_command": ["python3", "-m", "messaging_llm_bot.whatsapp_bridge", "receive", "--session", "data/whatsapp-personal"],
                  "send_command": ["python3", "-m", "messaging_llm_bot.whatsapp_bridge", "send", "--session", "data/whatsapp-personal", "--recipient", "{recipient}", "--message", "{message}", "--attachment", "{attachment}"]
                }
                """.strip(),
                encoding="utf-8",
            )

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_signup(str(config_dir))

        output = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("WhatsApp signup is concrete in this repo", output)
        self.assertIn("pip install playwright", output)
        self.assertIn("whatsapp.receive_command", output)
        self.assertIn("QR", output)
        self.assertIn("Linked Devices", output)
        self.assertIn("messaging_llm_bot.whatsapp_bridge pair", output)
        self.assertIn("python3 run_bot.py --config config", output)


if __name__ == "__main__":
    unittest.main()
