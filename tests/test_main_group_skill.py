import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_main_group_module():
    skill_path = Path("skills/main_group/__init__.py")
    spec = importlib.util.spec_from_file_location("main_group_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load main_group skill module")
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


class MainGroupSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_main_group_module()

    def test_saves_explicit_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(Path(td) / "workspace")
            result = self.module.run(
                workspace,
                {
                    "backend": "telegram",
                    "conversation_id": -1001234567890,
                    "name": "aaa group",
                },
            )

            self.assertEqual(result, "Saved main group (aaa group): telegram:-1001234567890")
            payload = json.loads(workspace.read_text("assistant/main_group.json"))
            self.assertEqual(payload["backend"], "telegram")
            self.assertEqual(payload["conversation_id"], "-1001234567890")
            self.assertEqual(payload["name"], "aaa group")

    def test_resolves_saved_contact_alias(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(Path(td) / "workspace")
            Path(td, "workspace").mkdir(parents=True, exist_ok=True)
            config = base_config()
            config["telegram"] = {
                "bot_token": "123:test",
                "mode": "bot",
                "allowed_chat_ids": [],
            }
            (Path(td) / "workspace" / "telegram.contacts").write_text(
                json.dumps({"aaa group": -100222333444}),
                encoding="utf-8",
            )
            config_path = workspace.resolve("config.json")
            write_config(config_path, config)

            result = self.module.run(
                workspace,
                {
                    "group": "aaa group",
                    "config_path": str(config_path),
                },
            )

            self.assertEqual(result, "Saved main group (aaa group): telegram:-100222333444")
            payload = json.loads(workspace.read_text("assistant/main_group.json"))
            self.assertEqual(payload["conversation_id"], "-100222333444")

    def test_defaults_to_repo_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(Path(td) / "workspace")
            Path(td, "workspace").mkdir(parents=True, exist_ok=True)
            config = base_config()
            config["telegram"] = {
                "bot_token": "123:test",
                "mode": "bot",
                "allowed_chat_ids": [],
            }
            (Path(td) / "workspace" / "telegram.contacts").write_text(
                json.dumps({"aaa group": -100222333444}),
                encoding="utf-8",
            )
            write_config(Path(td) / "config" / "llm.json", config["llm"])
            write_config(Path(td) / "config" / "runtime.json", config["runtime"])
            write_config(Path(td) / "config" / "telegram.json", config["telegram"])

            result = self.module.run(
                workspace,
                {
                    "group": "aaa group",
                },
            )

            self.assertEqual(result, "Saved main group (aaa group): telegram:-100222333444")


if __name__ == "__main__":
    unittest.main()
