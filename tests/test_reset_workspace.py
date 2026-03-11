import json
import tempfile
import unittest
from pathlib import Path

from reset_workspace import _gather_targets


class ResetWorkspaceTests(unittest.TestCase):
    def test_gather_targets_includes_workspace_and_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            (repo_root / "config.json").write_text(json.dumps({"runtime": {"workspace_dir": "workspace"}}), encoding="utf-8")

            targets = _gather_targets(repo_root)

        self.assertIn((repo_root / "workspace").resolve(), targets)
        self.assertIn((repo_root / "data").resolve(), targets)

    def test_gather_targets_includes_configured_session_path_outside_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            (repo_root / "config.json").write_text("{}", encoding="utf-8")
            (repo_root / "agent_config.json").write_text(
                json.dumps({"collectors": {"telegram_recent": {"user_client": {"session_path": "runtime_state/telegram_user"}}}}),
                encoding="utf-8",
            )

            targets = _gather_targets(repo_root)

        self.assertIn((repo_root / "runtime_state/telegram_user").resolve(), targets)
        self.assertIn((repo_root / "runtime_state/telegram_user.session").resolve(), targets)
        self.assertIn((repo_root / "runtime_state/telegram_user.session-journal").resolve(), targets)
        self.assertIn((repo_root / "runtime_state/telegram_user.updates.json").resolve(), targets)

    def test_gather_targets_includes_legacy_root_telegram_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            (repo_root / "config.json").write_text("{}", encoding="utf-8")

            targets = _gather_targets(repo_root)

        self.assertIn((repo_root / "telegram_user.session").resolve(), targets)
        self.assertIn((repo_root / "telegram_user.session-journal").resolve(), targets)
        self.assertIn((repo_root / "telegram_user.updates.json").resolve(), targets)

    def test_gather_targets_uses_runtime_collector_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            (repo_root / "config.json").write_text(
                json.dumps({"runtime": {"collector_config_path": "configs/agent.local.json"}}),
                encoding="utf-8",
            )
            (repo_root / "configs").mkdir()
            (repo_root / "configs/agent.local.json").write_text(
                json.dumps({"collectors": {"telegram_recent": {"user_client": {"session_path": "state/custom_telegram"}}}}),
                encoding="utf-8",
            )

            targets = _gather_targets(repo_root)

        self.assertIn((repo_root / "state/custom_telegram.session").resolve(), targets)
        self.assertIn((repo_root / "state/custom_telegram.session-journal").resolve(), targets)
        self.assertIn((repo_root / "state/custom_telegram.updates.json").resolve(), targets)

    def test_gather_targets_includes_main_config_telegram_session_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            (repo_root / "config.json").write_text(
                json.dumps({"telegram": {"session_path": "runtime_state/bot_user"}}),
                encoding="utf-8",
            )

            targets = _gather_targets(repo_root)

        self.assertIn((repo_root / "runtime_state/bot_user").resolve(), targets)
        self.assertIn((repo_root / "runtime_state/bot_user.session").resolve(), targets)
        self.assertIn((repo_root / "runtime_state/bot_user.session-journal").resolve(), targets)
        self.assertIn((repo_root / "runtime_state/bot_user.updates.json").resolve(), targets)
