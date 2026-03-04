import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant_framework.workspace import Workspace


def load_taskwarrior_module():
    skill_path = Path("skills/taskwarrior.py")
    spec = importlib.util.spec_from_file_location("taskwarrior_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load taskwarrior skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TaskwarriorSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_taskwarrior_module()
        self.workspace = Workspace(".")

    def test_add_requires_description(self) -> None:
        result = self.module.run(self.workspace, {"action": "add"})
        self.assertEqual(result, "Missing required arg `description` for action `add`.")

    def test_done_requires_id(self) -> None:
        result = self.module.run(self.workspace, {"action": "done"})
        self.assertEqual(result, "Missing required arg `id` for action `done`.")

    def test_modify_requires_id(self) -> None:
        result = self.module.run(self.workspace, {"action": "modify"})
        self.assertEqual(result, "Missing required arg `id` for action `modify`.")

    def test_modify_requires_field(self) -> None:
        result = self.module.run(self.workspace, {"action": "modify", "id": "2"})
        self.assertEqual(result, "Action `modify` requires at least one of: `project`, `due`.")

    def test_list_calls_task_list(self) -> None:
        with patch.object(self.module.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "1 Buy milk"
            run_mock.return_value.stderr = ""

            result = self.module.run(self.workspace, {"action": "list"})

            run_mock.assert_called_once_with(["task", "list"], capture_output=True, text=True, check=False)
            self.assertEqual(result, "1 Buy milk")

    def test_list_accepts_command_alias(self) -> None:
        with patch.object(self.module.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "1 Buy milk"
            run_mock.return_value.stderr = ""

            result = self.module.run(self.workspace, {"command": "list"})

            run_mock.assert_called_once_with(["task", "list"], capture_output=True, text=True, check=False)
            self.assertEqual(result, "1 Buy milk")

    def test_add_calls_task_add(self) -> None:
        with patch.object(self.module.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "Created task 5."
            run_mock.return_value.stderr = ""

            result = self.module.run(self.workspace, {"action": "add", "description": "Buy milk"})

            run_mock.assert_called_once_with(
                ["task", "add", "Buy milk"], capture_output=True, text=True, check=False
            )
            self.assertEqual(result, "Created task 5.")

    def test_add_supports_project_and_due(self) -> None:
        with patch.object(self.module.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "Created task 6."
            run_mock.return_value.stderr = ""

            result = self.module.run(
                self.workspace,
                {
                    "action": "add",
                    "description": "Pay rent",
                    "project": "Home",
                    "due": "tomorrow",
                },
            )

            run_mock.assert_called_once_with(
                ["task", "add", "project:Home", "due:tomorrow", "Pay rent"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result, "Created task 6.")

    def test_modify_calls_task_modify(self) -> None:
        with patch.object(self.module.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "Modified 1 task."
            run_mock.return_value.stderr = ""

            result = self.module.run(self.workspace, {"action": "modify", "id": "3", "project": "Ops"})

            run_mock.assert_called_once_with(
                ["task", "3", "modify", "project:Ops"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result, "Modified 1 task.")


if __name__ == "__main__":
    unittest.main()
