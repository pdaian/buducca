import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from assistant_framework.memory import calculate_next_run
from assistant_framework.workspace import Workspace


def load_memory_module():
    skill_path = Path("skills/memory/__init__.py")
    spec = importlib.util.spec_from_file_location("memory_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load memory skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MemorySkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_memory_module()

    def test_upsert_and_get_fact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(
                workspace,
                {
                    "action": "upsert",
                    "area": "facts",
                    "record": {"id": "timezone", "statement": "Home timezone is America/New_York"},
                },
            )
            payload = json.loads(result)
            self.assertEqual(payload["id"], "timezone")
            fetched = json.loads(self.module.run(workspace, {"action": "get", "area": "facts", "id": "timezone"}))
            self.assertEqual(fetched["statement"], "Home timezone is America/New_York")

    def test_invalid_task_due_at_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(
                workspace,
                {
                    "action": "upsert",
                    "area": "tasks",
                    "record": {"id": "x", "title": "Pay rent", "due_at": "tomorrow morning"},
                },
            )
            self.assertEqual(result, "Field `due_at` must be an ISO-8601 datetime.")

    def test_routine_defaults_next_run(self) -> None:
        next_run = calculate_next_run(
            {"frequency": "daily", "interval": 1, "hour": 9, "minute": 30, "timezone": "UTC"},
        )
        self.assertIn("T09:30:00+00:00", next_run)


if __name__ == "__main__":
    unittest.main()
