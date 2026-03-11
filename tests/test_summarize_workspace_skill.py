import importlib.util
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_summarize_workspace_module():
    skill_path = Path("skills/summarize_workspace/__init__.py")
    spec = importlib.util.spec_from_file_location("summarize_workspace_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load summarize_workspace skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SummarizeWorkspaceSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_summarize_workspace_module()

    def test_excludes_attachments_folder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("notes/todo.txt", "hello")
            workspace.write_text("attachments/2026-03-10/file.pdf", "pdf")

            result = self.module.run(workspace, {"max_items": 10})

            self.assertEqual(result, "- notes/todo.txt (5 bytes)")


if __name__ == "__main__":
    unittest.main()
