import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def test_read_write_append(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Workspace(td)
            ws.write_text("notes/a.txt", "hello")
            ws.append_text("notes/a.txt", " world")
            self.assertEqual(ws.read_text("notes/a.txt"), "hello world")

    def test_resolve_blocks_escape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Workspace(td)
            with self.assertRaises(ValueError):
                ws.resolve("../bad.txt")

    def test_resolve_blocks_sibling_prefix_escape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "workspace"
            sibling = Path(td) / "workspace-escape"
            root.mkdir()
            sibling.mkdir()
            ws = Workspace(root)
            with self.assertRaises(ValueError):
                ws.resolve("../workspace-escape/secret.txt")

    def test_directory_and_move_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Workspace(td)
            ws.write_text("src/a.txt", "hello")
            ws.create_dir("dst")

            moved_path = ws.move_file_to_dir("src/a.txt", "dst")
            self.assertEqual(moved_path, "dst/a.txt")
            self.assertEqual(ws.read_text("dst/a.txt"), "hello")

            ws.delete_dir("dst")
            self.assertFalse(ws.resolve("dst").exists())


if __name__ == "__main__":
    unittest.main()
