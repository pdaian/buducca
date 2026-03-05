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
            self.assertEqual(result, "Missing required arg `paths` (list).")

    def test_read_write_append(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)

            write_result = self.module.run(
                workspace,
                {
                    "action": "write",
                    "paths": ["notes/todo.txt", "notes/next.txt"],
                    "contents": ["hello", "today"],
                },
            )
            append_result = self.module.run(
                workspace,
                {
                    "action": "append",
                    "paths": ["notes/todo.txt", "notes/next.txt"],
                    "content": "!",
                },
            )
            read_result = self.module.run(
                workspace,
                {"action": "read", "paths": ["notes/todo.txt", "notes/next.txt"]},
            )

            self.assertEqual(
                write_result,
                "Wrote 5 character(s) to notes/todo.txt.\nWrote 5 character(s) to notes/next.txt.",
            )
            self.assertEqual(
                append_result,
                "Appended 1 character(s) to notes/todo.txt.\nAppended 1 character(s) to notes/next.txt.",
            )
            self.assertEqual(read_result, "notes/todo.txt:\nhello!\n\nnotes/next.txt:\ntoday!")

    def test_read_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"action": "read", "paths": ["missing.txt"]})
            self.assertEqual(result, "missing.txt: File not found")

    def test_directory_and_move_actions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)

            create_result = self.module.run(
                workspace,
                {"action": "create_dir", "directories": ["docs", "archive/2026"]},
            )
            self.assertEqual(create_result, "Created directory: docs\nCreated directory: archive/2026")

            self.module.run(
                workspace,
                {
                    "action": "write",
                    "paths": ["docs/a.txt", "docs/b.txt"],
                    "contents": ["a", "b"],
                },
            )

            move_result = self.module.run(
                workspace,
                {
                    "action": "move",
                    "paths": ["docs/a.txt", "docs/b.txt"],
                    "destination_dir": "archive/2026",
                },
            )
            self.assertEqual(
                move_result,
                "Moved docs/a.txt to archive/2026/a.txt.\nMoved docs/b.txt to archive/2026/b.txt.",
            )

            delete_result = self.module.run(
                workspace,
                {"action": "delete_dir", "directories": ["docs"]},
            )
            self.assertEqual(delete_result, "Deleted directory: docs")


if __name__ == "__main__":
    unittest.main()
