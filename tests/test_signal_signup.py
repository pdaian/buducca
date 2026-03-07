import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telegram_llm_bot.signal_signup import run_signup


class SignalSignupTests(unittest.TestCase):
    def test_uses_default_signal_link_command_and_writes_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.json"
            qr_path = Path(td) / "signal_qr.txt"
            config_path.write_text(
                json.dumps(
                    {
                        "signal": {
                            "device_name": "my-device",
                            "qr_output": str(qr_path),
                            "signup_timeout_seconds": 42,
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch("telegram_llm_bot.signal_signup.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "sgnl://linkdevice?uuid=test"
                run.return_value.stderr = ""

                code = run_signup(str(config_path))

            self.assertEqual(code, 0)
            self.assertEqual(qr_path.read_text(encoding="utf-8"), "sgnl://linkdevice?uuid=test")
            run.assert_called_once_with(
                ["signal-cli", "link", "-n", "my-device"],
                capture_output=True,
                text=True,
                check=False,
                timeout=42.0,
            )

    def test_uses_custom_link_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.json"
            qr_path = Path(td) / "signal_qr.txt"
            config_path.write_text(
                json.dumps(
                    {
                        "signal": {
                            "link_command": ["custom-signal", "link-now"],
                            "qr_output": str(qr_path),
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch("telegram_llm_bot.signal_signup.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ""
                run.return_value.stderr = "fallback-output"

                run_signup(str(config_path))

            run.assert_called_once_with(
                ["custom-signal", "link-now"],
                capture_output=True,
                text=True,
                check=False,
                timeout=120.0,
            )
            self.assertEqual(qr_path.read_text(encoding="utf-8"), "fallback-output")

    def test_timeout_returns_124_and_writes_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.json"
            qr_path = Path(td) / "signal_qr.txt"
            config_path.write_text(
                json.dumps({"signal": {"qr_output": str(qr_path)}}),
                encoding="utf-8",
            )

            timeout_exc = subprocess.TimeoutExpired(
                cmd=["signal-cli", "link", "-n", "buducca"],
                timeout=120,
                output="partial-link",
                stderr="",
            )

            with patch("telegram_llm_bot.signal_signup.subprocess.run", side_effect=timeout_exc):
                code = run_signup(str(config_path))

            self.assertEqual(code, 124)
            self.assertEqual(qr_path.read_text(encoding="utf-8"), "partial-link")

    def test_timeout_with_bytes_output_is_decoded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.json"
            qr_path = Path(td) / "signal_qr.txt"
            config_path.write_text(
                json.dumps({"signal": {"qr_output": str(qr_path)}}),
                encoding="utf-8",
            )

            timeout_exc = subprocess.TimeoutExpired(
                cmd=["signal-cli", "link", "-n", "buducca"],
                timeout=120,
                output=b"partial-link-from-bytes",
                stderr=b"",
            )

            with patch("telegram_llm_bot.signal_signup.subprocess.run", side_effect=timeout_exc):
                code = run_signup(str(config_path))

            self.assertEqual(code, 124)
            self.assertEqual(qr_path.read_text(encoding="utf-8"), "partial-link-from-bytes")

    def test_invalid_timeout_falls_back_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.json"
            qr_path = Path(td) / "signal_qr.txt"
            config_path.write_text(
                json.dumps(
                    {
                        "signal": {
                            "signup_timeout_seconds": "inf",
                            "qr_output": str(qr_path),
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch("telegram_llm_bot.signal_signup.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "ok"
                run.return_value.stderr = ""

                run_signup(str(config_path))

            run.assert_called_once_with(
                ["signal-cli", "link", "-n", "buducca"],
                capture_output=True,
                text=True,
                check=False,
                timeout=120.0,
            )


if __name__ == "__main__":
    unittest.main()
