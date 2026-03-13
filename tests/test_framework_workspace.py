import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def test_init_does_not_create_root_until_needed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "workspace"
            Workspace(root)
            self.assertFalse(root.exists())

    def test_read_write_append(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Workspace(td)
            ws.write_text("notes/a.txt", "hello")
            ws.append_text("notes/a.txt", " world")
            self.assertEqual(ws.read_text("notes/a.txt"), "hello world")

    def test_data_root_points_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "workspace"
            ws = Workspace(root)
            self.assertEqual(ws.data_root(), Path(td) / "data")

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

    def test_move_copy_and_delete_path_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Workspace(td)
            ws.write_text("src/a.txt", "hello")
            ws.write_text("src/nested/b.txt", "world")

            moved_path = ws.move_path("src/a.txt", "renamed/a.txt")
            copied_path = ws.copy_path("src/nested", "copied/nested")

            self.assertEqual(moved_path, "renamed/a.txt")
            self.assertEqual(copied_path, "copied/nested")
            self.assertEqual(ws.read_text("renamed/a.txt"), "hello")
            self.assertEqual(ws.read_text("copied/nested/b.txt"), "world")

            ws.delete_path("renamed/a.txt")
            ws.delete_path("copied/nested")
            self.assertFalse(ws.resolve("renamed/a.txt").exists())
            self.assertFalse(ws.resolve("copied/nested").exists())


if __name__ == "__main__":
    unittest.main()
