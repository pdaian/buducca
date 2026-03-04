import importlib.util
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_file_module():
    skill_path = Path("skills/file.py")
    spec = importlib.util.spec_from_file_location("file_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load file skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FileSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_file_module()

    def test_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"action": "read"})
            self.assertEqual(result, "Missing required arg `path`.")

    def test_read_write_append(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)

            write_result = self.module.run(
                workspace,
                {"action": "write", "path": "notes/todo.txt", "content": "hello"},
            )
            append_result = self.module.run(
                workspace,
                {"action": "append", "path": "notes/todo.txt", "content": " world"},
            )
            read_result = self.module.run(workspace, {"action": "read", "path": "notes/todo.txt"})

            self.assertEqual(write_result, "Wrote 5 character(s) to notes/todo.txt.")
            self.assertEqual(append_result, "Appended 6 character(s) to notes/todo.txt.")
            self.assertEqual(read_result, "hello world")

    def test_read_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"action": "read", "path": "missing.txt"})
            self.assertEqual(result, "File not found: missing.txt")


if __name__ == "__main__":
    unittest.main()
