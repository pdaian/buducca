import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_message_send_module():
    skill_path = Path("skills/message_send/__init__.py")
    spec = importlib.util.spec_from_file_location("message_send_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load message_send skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_config(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def base_config() -> dict:
    return {
        "llm": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "test-key",
            "model": "gpt-4o-mini",
        },
        "runtime": {
            "request_timeout_seconds": 30,
            "skills_dir": "skills",
        },
    }


class MessageSendSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_message_send_module()

    def test_sends_single_telegram_message(self) -> None:
        sent: list[tuple[int, str]] = []

        class FakeTelegramClient:
            def __init__(self, bot_token, http_client) -> None:
                self.bot_token = bot_token
                self.http_client = http_client

            def send_message(self, chat_id: int, text: str) -> None:
                sent.append((chat_id, text))

        class FakeHttpClient:
            def __init__(self, timeout_seconds: float) -> None:
                self.timeout_seconds = timeout_seconds

        self.module.TelegramClient = FakeTelegramClient
        self.module.HttpClient = FakeHttpClient

        with tempfile.TemporaryDirectory() as td:
            config = base_config()
            config["telegram"] = {
                "bot_token": "123:test",
                "mode": "bot",
                "allowed_chat_ids": [],
            }
            config_path = Path(td) / "config.json"
            write_config(config_path, config)

            workspace = Workspace(Path(td) / "workspace")
            result = self.module.run(
                workspace,
                {
                    "backend": "telegram",
                    "recipient": 123456789,
                    "message": "hello",
                    "config_path": str(config_path),
                },
            )

            self.assertEqual(result, "telegram: sent to 123456789.")
            self.assertEqual(sent, [(123456789, "hello")])

    def test_supports_multi_backend_fanout_with_aliases(self) -> None:
        signal_sent: list[tuple[str, str]] = []
        fi_sent: list[tuple[str, str]] = []

        class FakeSignalClient:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def send_message(self, recipient: str, text: str) -> None:
                signal_sent.append((recipient, text))

        class FakeGoogleFiClient:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def send_message(self, recipient: str, text: str) -> None:
                fi_sent.append((recipient, text))

        self.module.SignalClient = FakeSignalClient
        self.module.GoogleFiClient = FakeGoogleFiClient

        with tempfile.TemporaryDirectory() as td:
            config = base_config()
            config["signal"] = {
                "account": "+15550000000",
                "receive_command": ["signal-cli", "receive"],
                "send_command": ["signal-cli", "send"],
            }
            config["google_fi"] = {
                "account": "personal",
                "receive_command": ["python3", "-m", "messaging_llm_bot.google_fi_client", "receive"],
                "send_command": ["python3", "-m", "messaging_llm_bot.google_fi_client", "send"],
            }
            config_path = Path(td) / "config.json"
            write_config(config_path, config)

            workspace = Workspace(Path(td) / "workspace")
            result = self.module.run(
                workspace,
                {
                    "backend": ["signal", "fi"],
                    "recipients": {
                        "signal": "+15551234567",
                        "fi": "+15557654321",
                    },
                    "message": "ping",
                    "config_path": str(config_path),
                },
            )

            self.assertEqual(
                result,
                "Sent 2 of 2 requested message(s).\n"
                "signal: sent to +15551234567.\n"
                "google_fi: sent to +15557654321.",
            )
            self.assertEqual(signal_sent, [("+15551234567", "ping")])
            self.assertEqual(fi_sent, [("+15557654321", "ping")])

    def test_rejects_read_only_backend(self) -> None:
        class FakeSignalClient:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def send_message(self, recipient: str, text: str) -> None:
                raise AssertionError("send_message should not be called for read-only backends")

        self.module.SignalClient = FakeSignalClient

        with tempfile.TemporaryDirectory() as td:
            config = base_config()
            config["signal"] = {
                "account": "+15550000000",
                "receive_command": ["signal-cli", "receive"],
                "send_command": ["signal-cli", "send"],
                "read_only": True,
            }
            config_path = Path(td) / "config.json"
            write_config(config_path, config)

            workspace = Workspace(Path(td) / "workspace")
            result = self.module.run(
                workspace,
                {
                    "backend": "signal",
                    "recipient": "+15551234567",
                    "message": "hello",
                    "config_path": str(config_path),
                },
            )

            self.assertEqual(result, "signal: backend is configured read-only; outbound sends are disabled.")

    def test_requires_per_backend_recipient_for_fanout(self) -> None:
        class FakeSignalClient:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def send_message(self, recipient: str, text: str) -> None:
                raise RuntimeError("signal transport unavailable")

        class FakeWhatsAppClient:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def send_message(self, recipient: str, text: str) -> None:
                raise AssertionError("whatsapp send_message should not be called without a recipient")

        self.module.SignalClient = FakeSignalClient
        self.module.WhatsAppClient = FakeWhatsAppClient

        with tempfile.TemporaryDirectory() as td:
            config = base_config()
            config["signal"] = {
                "account": "+15550000000",
                "receive_command": ["signal-cli", "receive"],
                "send_command": ["signal-cli", "send"],
            }
            config["whatsapp"] = {
                "account": "personal",
                "receive_command": ["python3", "-m", "messaging_llm_bot.whatsapp_client", "receive"],
                "send_command": ["python3", "-m", "messaging_llm_bot.whatsapp_client", "send"],
            }
            config_path = Path(td) / "config.json"
            write_config(config_path, config)

            workspace = Workspace(Path(td) / "workspace")
            result = self.module.run(
                workspace,
                {
                    "backend": ["signal", "whatsapp"],
                    "recipients": {"signal": "+15551234567"},
                    "message": "hello",
                    "config_path": str(config_path),
                },
            )

            self.assertEqual(
                result,
                "Sent 0 of 2 requested message(s).\n"
                "signal: send failed: signal transport unavailable\n"
                "whatsapp: Missing required recipient for backend `whatsapp`.",
            )

    def test_build_action_requires_approval(self) -> None:
        action = self.module.build_action({"backend": "telegram", "recipient": 1, "message": "hi"})
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.name, "message_send.send")
        self.assertTrue(action.requires_approval)


if __name__ == "__main__":
    unittest.main()
