import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from messaging_llm_bot.signal_signup import run_signup


class SignalSignupTests(unittest.TestCase):
    def test_prints_signal_cli_setup_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.json"
            config_path.write_text("{}", encoding="utf-8")

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_signup(str(config_path))

        output = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Signal signup is no longer automated", output)
        self.assertIn("Phone number", output)
        self.assertIn("QR", output)
        self.assertIn("Registration-with-signal-cli", output)


if __name__ == "__main__":
    unittest.main()
