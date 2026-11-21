import unittest
from copy import deepcopy
from unittest.mock import patch

from messaging_llm_bot.config import load_config


class ConfigValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = {
            "telegram": {"bot_token": "test"},
            "llm": {"base_url": "https://example.invalid/v1", "api_key": "test", "model": "test"},
            "runtime": {},
        }

    def load(self):
        with patch("messaging_llm_bot.config.load_json_path", return_value=deepcopy(self.raw)):
            return load_config("config")

    def test_legacy_configs_return_full_skill_results_by_default(self) -> None:
        config = self.load()
        self.assertIsNone(config.runtime.max_llm_requests_per_turn)
        self.assertIsNone(config.runtime.max_repeated_skill_results)
        self.assertIsNone(config.runtime.max_skill_result_chars)

    def test_checked_in_example_is_valid(self) -> None:
        config = load_config("config.example")
        self.assertEqual(config.runtime.max_llm_requests_per_turn, 24)
        self.assertEqual(config.runtime.max_repeated_skill_results, 3)
        self.assertIsNone(config.runtime.max_skill_result_chars)

    def test_limits_accept_null_and_their_minimum(self) -> None:
        for name, minimum in (("max_llm_requests_per_turn", 1), ("max_repeated_skill_results", 2), ("max_skill_result_chars", 512)):
            for value in (None, minimum):
                with self.subTest(name=name, value=value):
                    self.raw["runtime"] = {name: value}
                    self.assertEqual(getattr(self.load().runtime, name), value)

    def test_invalid_limits_fail_at_config_load(self) -> None:
        for name, minimum in (("max_llm_requests_per_turn", 1), ("max_repeated_skill_results", 2), ("max_skill_result_chars", 512)):
            for value in (True, "3", 1.5, -1, minimum - 1, float("inf"), float("nan")):
                with self.subTest(name=name, value=value):
                    self.raw["runtime"] = {name: value}
                    with self.assertRaisesRegex(ValueError, f"runtime.{name}"):
                        self.load()

    def test_section_types_and_unknown_settings_have_clear_errors(self) -> None:
        for section in ("runtime", "llm", "telegram", "signal", "android", "whatsapp"):
            for value in ([], "bad", {"typo": True}):
                with self.subTest(section=section, value=value):
                    original = deepcopy(self.raw)
                    self.raw[section] = value
                    with self.assertRaisesRegex(ValueError, section):
                        self.load()
                    self.raw = original

    def test_runtime_and_frontend_types_are_not_coerced(self) -> None:
        for section, field, value in (
            ("runtime", "max_concurrent_requests", True),
            ("runtime", "max_skill_chain_steps", 1.5),
            ("runtime", "workspace_dir", None),
            ("runtime", "debug", "false"),
            ("runtime", "file_skill_actions", "read"),
            ("telegram", "read_only", "false"),
            ("telegram", "allowed_chat_ids", [True]),
            ("telegram", "allowed_chat_ids", ["123"]),
            ("telegram", "bot_token", 123),
            ("llm", "history_messages", 1.5),
        ):
            with self.subTest(section=section, field=field):
                original = deepcopy(self.raw)
                self.raw[section][field] = value
                with self.assertRaisesRegex(ValueError, f"{section}.{field}"):
                    self.load()
                self.raw = original

    def test_nonfinite_numbers_are_rejected(self) -> None:
        for section, field in (("llm", "temperature"), ("runtime", "request_timeout_seconds"), ("telegram", "poll_interval_seconds")):
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(section=section, field=field, value=value):
                    original = deepcopy(self.raw)
                    self.raw[section][field] = value
                    with self.assertRaisesRegex(ValueError, f"{section}.{field}"):
                        self.load()
                    self.raw = original

    def test_runner_urls_and_invalid_streaming_options_fail_before_startup(self) -> None:
        for field, value in (
            ("base_url", "file:///tmp/model"), ("base_url", "relative/path"),
            ("base_url", "https://host:invalid/v1"), ("base_url", "https://host:99999/v1"),
            ("base_url", "https://bad host/v1"), ("base_url", "https://host/v1\n"),
            ("base_url", "https://user:password@host/v1"), ("base_url", "https://host/v1?key=secret"),
            ("endpoint_path", "https://other.invalid"), ("endpoint_path", "//other.invalid"),
            ("endpoint_path", "//["), ("endpoint_path", "/chat completions"),
            ("extra_body", {"stream": 1}), ("extra_body", {"stream": "false"}),
            ("extra_body", {"stream_options": None}), ("extra_body", {"stream_options": []}),
            ("extra_body", {"stream_options": {"include_usage": "true"}}),
            ("extra_body", {"temperature": float("nan")}), ("extra_body", {"model": "override"}),
        ):
            with self.subTest(field=field, value=value):
                original = deepcopy(self.raw)
                self.raw["llm"][field] = value
                with self.assertRaisesRegex(ValueError, field):
                    self.load()
                self.raw = original

    def test_local_urls_null_sampling_and_false_streaming_remain_supported(self) -> None:
        self.raw["llm"].update({
            "base_url": "http://[::1]:8000/v1", "temperature": None, "max_tokens": None,
            "extra_body": {"stream": False, "max_completion_tokens": 1000},
        })
        self.raw["runtime"]["_comment"] = "ignored"
        self.assertIsNone(self.load().llm.temperature)

    def test_streaming_and_usage_options_are_supported(self) -> None:
        self.raw["llm"]["extra_body"] = {"stream": True, "stream_options": {"include_usage": True}}
        self.assertEqual(self.load().llm.extra_body, self.raw["llm"]["extra_body"])

    def test_bad_runner_types_and_unknown_fields_identify_the_index(self) -> None:
        for runner in ("bad", {"typo": True}, {"api_key": 123}):
            with self.subTest(runner=runner):
                self.raw["llm"]["runners"] = [runner]
                with self.assertRaisesRegex(ValueError, r"llm.runners\[0\]"):
                    self.load()
