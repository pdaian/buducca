import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_attach_file_module():
    skill_path = Path("skills/attach_file/__init__.py")
    spec = importlib.util.spec_from_file_location("attach_file_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load attach_file skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_config(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


class AttachFileSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_attach_file_module()

    def test_sends_single_telegram_attachment(self) -> None:
        sent: list[tuple[int, str, str]] = []

        class FakeTelegramClient:
            def __init__(self, bot_token, http_client) -> None:
                self.bot_token = bot_token
                self.http_client = http_client

            def send_file(self, chat_id: int, file_path: str, caption: str | None = None) -> None:
                sent.append((chat_id, file_path, caption or ""))

        class FakeHttpClient:
            def __init__(self, timeout_seconds: float) -> None:
                self.timeout_seconds = timeout_seconds

        self.module.TelegramClient = FakeTelegramClient
        self.module.HttpClient = FakeHttpClient

        with tempfile.TemporaryDirectory() as td:
            config = base_config()
            config["telegram"] = {"bot_token": "123:test", "mode": "bot"}
            workspace = Workspace(Path(td) / "workspace")
            config_path = workspace.resolve("config.json")
            write_config(config_path, config)
            workspace.write_text("reports/latest.txt", "hello")
            result = self.module.run(
                workspace,
                {
                    "backend": "telegram",
                    "recipient": 123456789,
                    "path": "reports/latest.txt",
                    "caption": "Latest",
                    "config_path": str(config_path),
                },
            )

            self.assertEqual(result, "telegram: attached reports/latest.txt to 123456789.")
            self.assertEqual(sent, [(123456789, str(workspace.resolve("reports/latest.txt")), "Latest")])

    def test_requires_existing_workspace_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = base_config()
            config["telegram"] = {"bot_token": "123:test", "mode": "bot"}
            workspace = Workspace(Path(td) / "workspace")
            config_path = workspace.resolve("config.json")
            write_config(config_path, config)
            result = self.module.run(
                workspace,
                {
                    "backend": "telegram",
                    "recipient": 123456789,
                    "path": "missing.txt",
                    "config_path": str(config_path),
                },
            )

            self.assertEqual(result, "File not found: missing.txt")

    def test_rejects_config_path_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = base_config()
            config["telegram"] = {"bot_token": "123:test", "mode": "bot"}
            config_path = Path(td) / "config.json"
            write_config(config_path, config)

            workspace = Workspace(Path(td) / "workspace")
            workspace.write_text("reports/latest.txt", "hello")
            result = self.module.run(
                workspace,
                {
                    "backend": "telegram",
                    "recipient": 123456789,
                    "path": "reports/latest.txt",
                    "config_path": "../config.json",
                },
            )

            self.assertEqual(result, "Path escapes workspace: ../config.json")

    def test_defaults_to_repo_config_directory(self) -> None:
        sent: list[tuple[int, str, str]] = []

        class FakeTelegramClient:
            def __init__(self, bot_token, http_client) -> None:
                self.bot_token = bot_token
                self.http_client = http_client

            def send_file(self, chat_id: int, file_path: str, caption: str | None = None) -> None:
                sent.append((chat_id, file_path, caption or ""))

        class FakeHttpClient:
            def __init__(self, timeout_seconds: float) -> None:
                self.timeout_seconds = timeout_seconds

        self.module.TelegramClient = FakeTelegramClient
        self.module.HttpClient = FakeHttpClient

        with tempfile.TemporaryDirectory() as td:
            config = base_config()
            config["telegram"] = {"bot_token": "123:test", "mode": "bot"}
            workspace = Workspace(Path(td) / "workspace")
            write_config(Path(td) / "config" / "llm.json", config["llm"])
            write_config(Path(td) / "config" / "runtime.json", config["runtime"])
            write_config(Path(td) / "config" / "telegram.json", config["telegram"])
            workspace.write_text("reports/latest.txt", "hello")

            result = self.module.run(
                workspace,
                {
                    "backend": "telegram",
                    "recipient": 123456789,
                    "path": "reports/latest.txt",
                    "caption": "Latest",
                },
            )

            self.assertEqual(result, "telegram: attached reports/latest.txt to 123456789.")
            self.assertEqual(sent, [(123456789, str(workspace.resolve("reports/latest.txt")), "Latest")])
