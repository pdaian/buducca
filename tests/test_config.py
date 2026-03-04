import json
import tempfile
import unittest
from pathlib import Path

from telegram_llm_bot.config import load_config


class ConfigTests(unittest.TestCase):
    def test_invalid_missing_token(self) -> None:
        data = {
            "telegram": {"bot_token": "", "long_poll_timeout_seconds": 10},
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


    def test_runtime_debug_defaults_to_false(self) -> None:
        data = {
            "telegram": {"bot_token": "t", "long_poll_timeout_seconds": 10},
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
            "runtime": {},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            config = load_config(path)
            self.assertFalse(config.runtime.debug)


    def test_runtime_timeout_must_be_positive(self) -> None:
        data = {
            "telegram": {"bot_token": "t", "long_poll_timeout_seconds": 10},
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
            "runtime": {"request_timeout_seconds": 0},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_voice_notes_require_transcribe_command(self) -> None:
        data = {
            "telegram": {"bot_token": "t", "long_poll_timeout_seconds": 10},
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
            "runtime": {"enable_voice_notes": True, "voice_transcribe_command": []},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
