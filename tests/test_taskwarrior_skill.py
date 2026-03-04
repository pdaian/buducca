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

    def test_list_calls_task_list(self) -> None:
        with patch.object(self.module.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "1 Buy milk"
            run_mock.return_value.stderr = ""

            result = self.module.run(self.workspace, {"action": "list"})

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


if __name__ == "__main__":
    unittest.main()
