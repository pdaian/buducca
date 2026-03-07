import importlib.util
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_learn_module():
    skill_path = Path("skills/learn/__init__.py")
    spec = importlib.util.spec_from_file_location("learn_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load learn skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LearnSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_learn_module()

    def test_missing_learning(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {})
            self.assertEqual(result, "Missing required arg `learning` (string).")

    def test_appends_learning_line(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"learning": "Use bullet points for summaries."})
            self.assertEqual(result, "Learned: Use bullet points for summaries.")
            self.assertEqual(
                workspace.read_text("learnings"),
                "Use bullet points for summaries.\n",
            )

    def test_fallback_keys_and_single_line_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"text": "Remember this\nfor later"})
            self.assertEqual(result, "Learned: Remember this for later")
            self.assertEqual(workspace.read_text("learnings"), "Remember this for later\n")


if __name__ == "__main__":
    unittest.main()
