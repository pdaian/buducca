import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from messaging_llm_bot.whatsapp_signup import run_signup


class WhatsAppSignupTests(unittest.TestCase):
    def test_prints_whatsapp_qr_setup_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.json"
            config_path.write_text("{}", encoding="utf-8")

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_signup(str(config_path))

        output = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("WhatsApp signup is not automated", output)
        self.assertIn("QR", output)
        self.assertIn("Linked Devices", output)
        self.assertIn("receive_command", output)


if __name__ == "__main__":
    unittest.main()
