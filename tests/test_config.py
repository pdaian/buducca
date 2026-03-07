import json
import tempfile
import unittest
import warnings
from pathlib import Path

from telegram_llm_bot.config import load_config


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

    def test_signal_frontend_and_signal_collector_collision_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            config_path = tmpdir / "config.json"
            agent_config_path = tmpdir / "agent_config.json"

            config_path.write_text(
                json.dumps(
                    {
                        "signal": {"account": "+15550001111"},
                        "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
                    }
                ),
                encoding="utf-8",
            )
            agent_config_path.write_text(
                json.dumps(
                    {
                        "collectors": {
                            "signal_messages": {
                                "accounts": [
                                    {"name": "primary", "device_name": "+15550001111"},
                                    {"name": "secondary", "device_name": "+15550001112"},
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "consumer contention"):
                load_config(config_path)

    def test_signal_frontend_and_signal_collector_collision_can_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            config_path = tmpdir / "config.json"
            agent_config_path = tmpdir / "agent_config.json"

            config_path.write_text(
                json.dumps(
                    {
                        "signal": {"account": "+15550001111"},
                        "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
                        "runtime": {"allow_signal_collector_device_collision": True},
                    }
                ),
                encoding="utf-8",
            )
            agent_config_path.write_text(
                json.dumps(
                    {
                        "collectors": {
                            "signal_messages": {
                                "accounts": [{"name": "primary", "device_name": "+15550001111"}]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                config = load_config(config_path)

            self.assertIsNotNone(config.signal)
            self.assertTrue(
                any("consumer contention" in str(w.message) for w in captured),
                "expected collision warning when override is enabled",
            )

    def test_signal_collector_collision_uses_explicit_agent_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            config_path = tmpdir / "config.json"
            nested = tmpdir / "configs"
            nested.mkdir()
            agent_config_path = nested / "custom_agent_config.json"

            config_path.write_text(
                json.dumps(
                    {
                        "signal": {"account": "+15550001111"},
                        "llm": {"base_url": "https://x", "api_key": "k", "model": "m"},
                    }
                ),
                encoding="utf-8",
            )
            agent_config_path.write_text(
                json.dumps(
                    {
                        "collectors": {
                            "signal_messages": {
                                "accounts": [{"name": "primary", "device_name": "+15550001111"}]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "collectors.signal_messages.accounts"):
                load_config(config_path, agent_config_path="configs/custom_agent_config.json")


if __name__ == "__main__":
    unittest.main()
