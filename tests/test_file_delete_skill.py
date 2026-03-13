import importlib.util
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_file_delete_module():
    skill_path = Path("skills/file_delete/__init__.py")
    spec = importlib.util.spec_from_file_location("file_delete_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load file_delete skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FileDeleteSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_file_delete_module()

    def test_missing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {})
            self.assertEqual(result, "Missing required arg `paths` (list).")

    def test_delete_files_from_list(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("notes/today.txt", "today")
            workspace.write_text("notes/tomorrow.txt", "tomorrow")

            result = self.module.run(
                workspace,
                {"paths": ["notes/today.txt", "notes/tomorrow.txt"]},
            )

            self.assertEqual(
                result,
                "Deleted file: notes/today.txt\nDeleted file: notes/tomorrow.txt",
            )
            self.assertFalse(workspace.resolve("notes/today.txt").exists())
            self.assertFalse(workspace.resolve("notes/tomorrow.txt").exists())

    def test_workspace_prefix_is_filtered_from_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("notes/today.txt", "today")

            result = self.module.run(
                workspace,
                {"paths": ["workspace/notes/today.txt"]},
            )

            self.assertEqual(result, "Deleted file: notes/today.txt")
            self.assertFalse(workspace.resolve("notes/today.txt").exists())

    def test_workspace_segment_inside_a_path_is_not_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("notes/workspace/today.txt", "today")

            result = self.module.run(
                workspace,
                {"paths": ["notes/workspace/today.txt"]},
            )

            self.assertEqual(result, "Deleted file: notes/workspace/today.txt")
            self.assertFalse(workspace.resolve("notes/workspace/today.txt").exists())

    def test_missing_file_and_directory_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.create_dir("notes")

            result = self.module.run(
                workspace,
                {"paths": ["missing.txt", "notes"]},
            )

            self.assertEqual(result, "missing.txt: File not found\nnotes: Not a file")

    def test_rejects_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)

            result = self.module.run(
                workspace,
                {"paths": ["../outside.txt"]},
            )

            self.assertEqual(result, "Path escapes workspace: ../outside.txt")


if __name__ == "__main__":
    unittest.main()
