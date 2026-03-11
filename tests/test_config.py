import json
import tempfile
import unittest
from pathlib import Path

from messaging_llm_bot.config import load_config


class ConfigTests(unittest.TestCase):
    def test_requires_at_least_one_frontend(self) -> None:
        data = {
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


    def test_invalid_json_includes_location_and_pointer(self) -> None:
        bad_json = '{"telegram": {"bot_token": "t"\n "llm": {"base_url": "https://x", "api_key": "k", "model": "m"}}'
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(bad_json, encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_config(path)
            msg = str(ctx.exception)
            self.assertIn("Invalid JSON in config file", msg)
            self.assertIn("line 2, column 2", msg)
            self.assertIn("^", msg)

    def test_missing_llm_section_has_friendly_error(self) -> None:
        data = {
            "telegram": {"bot_token": "t", "long_poll_timeout_seconds": 10},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_config(path)
            self.assertEqual(str(ctx.exception), "Missing required top-level section: llm")

    def test_signal_only_config_is_valid(self) -> None:
        data = {
            "signal": {"account": "+15550001"},
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            config = load_config(path)
            self.assertIsNotNone(config.signal)
            self.assertIsNone(config.telegram)

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

    def test_llm_timezone_defaults_to_new_york(self) -> None:
        data = {
            "telegram": {"bot_token": "t", "long_poll_timeout_seconds": 10},
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.llm.system_prompt_timezone, "America/New_York")

    def test_llm_timezone_must_be_valid(self) -> None:
        data = {
            "telegram": {"bot_token": "t", "long_poll_timeout_seconds": 10},
            "llm": {
                "base_url": "https://x",
                "api_key": "k",
                "model": "m",
                "system_prompt_timezone": "Mars/Olympus_Mons",
            },
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


    def test_signal_group_sender_override_ids_load(self) -> None:
        data = {
            "signal": {
                "account": "+15550001",
                "allowed_sender_ids": ["+15551112222"],
                "allowed_group_ids_when_sender_not_allowed": ["AQi7f+/4S3mQv6s5hN2xwQ=="],
            },
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            config = load_config(path)
            self.assertIsNotNone(config.signal)
            self.assertEqual(config.signal.allowed_group_ids_when_sender_not_allowed, ["AQi7f+/4S3mQv6s5hN2xwQ=="])


    def test_telegram_user_mode_requires_api_credentials(self) -> None:
        data = {
            "telegram": {"mode": "user", "bot_token": ""},
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_telegram_user_mode_is_valid_with_api_credentials(self) -> None:
        data = {
            "telegram": {
                "mode": "user",
                "api_id": 123,
                "api_hash": "h",
                "session_path": "data/telegram_user",
            },
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            config = load_config(path)
            self.assertIsNotNone(config.telegram)
            self.assertEqual(config.telegram.mode, "user")
    def test_whatsapp_only_config_is_valid(self) -> None:
        data = {
            "whatsapp": {
                "account": "personal",
                "receive_command": ["python3", "recv.py"],
                "send_command": ["python3", "send.py", "{recipient}", "{message}"],
            },
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            config = load_config(path)
            self.assertIsNotNone(config.whatsapp)
            self.assertIsNone(config.telegram)
            self.assertIsNone(config.signal)

    def test_whatsapp_poll_interval_must_be_non_negative(self) -> None:
        data = {
            "whatsapp": {
                "account": "personal",
                "poll_interval_seconds": -1,
                "receive_command": ["python3", "recv.py"],
                "send_command": ["python3", "send.py", "{recipient}", "{message}"],
            },
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


    def test_runtime_file_skill_actions_must_not_be_empty(self) -> None:
        data = {
            "telegram": {"bot_token": "t", "long_poll_timeout_seconds": 10},
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
            "runtime": {"file_skill_actions": []},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_runtime_message_send_skill_defaults_to_disabled(self) -> None:
        data = {
            "telegram": {"bot_token": "t", "long_poll_timeout_seconds": 10},
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            config = load_config(path)
            self.assertFalse(config.runtime.enable_message_send_skill)

    def test_runtime_comment_keys_are_ignored(self) -> None:
        data = {
            "telegram": {"bot_token": "t", "long_poll_timeout_seconds": 10},
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
            "runtime": {
                "enable_message_send_skill": True,
                "_warning_enable_message_send_skill": "Warning text.",
            },
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            config = load_config(path)
            self.assertTrue(config.runtime.enable_message_send_skill)


    def test_google_fi_only_config_is_valid(self) -> None:
        data = {
            "google_fi": {
                "account": "personal",
                "receive_command": ["python3", "recv.py"],
                "send_command": ["python3", "send.py", "{recipient}", "{message}"],
            },
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            config = load_config(path)
            self.assertIsNotNone(config.google_fi)
            self.assertIsNone(config.telegram)
            self.assertIsNone(config.signal)

    def test_google_fi_poll_interval_must_be_non_negative(self) -> None:
        data = {
            "google_fi": {
                "account": "personal",
                "poll_interval_seconds": -1,
                "receive_command": ["python3", "recv.py"],
                "send_command": ["python3", "send.py", "{recipient}", "{message}"],
            },
            "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "c.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
