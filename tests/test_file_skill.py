import importlib.util
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_file_module():
    skill_path = Path("skills/file/__init__.py")
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

    def test_read_supports_head_tail_and_range_modes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module.run(
                workspace,
                {
                    "action": "write",
                    "paths": ["notes/log.txt"],
                    "content": "line1\nline2\nline3\nline4",
                },
            )

            head_result = self.module.run(
                workspace,
                {"action": "read", "paths": ["notes/log.txt"], "read_mode": "head", "read_line_limit": 2},
            )
            tail_result = self.module.run(
                workspace,
                {"action": "read", "paths": ["notes/log.txt"], "read_line_limit": 2},
            )
            range_result = self.module.run(
                workspace,
                {
                    "action": "read",
                    "paths": ["notes/log.txt"],
                    "read_mode": "range",
                    "start_line": 2,
                    "end_line": 3,
                },
            )

            self.assertEqual(head_result, "notes/log.txt:\nline1\nline2")
            self.assertEqual(tail_result, "notes/log.txt:\nline3\nline4")
            self.assertEqual(range_result, "notes/log.txt:\nline2\nline3")

    def test_read_validates_range_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module.run(
                workspace,
                {"action": "write", "paths": ["notes/log.txt"], "content": "line1\nline2"},
            )

            missing_limit = self.module.run(
                workspace,
                {"action": "read", "paths": ["notes/log.txt"], "read_mode": "head"},
            )
            invalid_range = self.module.run(
                workspace,
                {
                    "action": "read",
                    "paths": ["notes/log.txt"],
                    "read_mode": "range",
                    "start_line": 3,
                    "end_line": 2,
                },
            )

            self.assertEqual(missing_limit, "`read_line_limit` is required for read_mode `head`.")
            self.assertEqual(invalid_range, "`end_line` must be greater than or equal to `start_line`.")

    def test_read_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {"action": "read", "paths": ["missing.txt"]})
            self.assertEqual(result, "missing.txt: File not found")

    def test_list_browses_directory_contents(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("docs/a.txt", "a")
            workspace.write_text("docs/nested/b.txt", "b")
            workspace.write_text(".hidden/secret.txt", "secret")

            result = self.module.run(workspace, {"action": "list", "path": "docs"})

            self.assertEqual(
                result,
                "Browsing docs: showing 2 entrie(s).\ndocs/nested/\ndocs/a.txt",
            )

    def test_list_defaults_to_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("docs/a.txt", "a")

            result = self.module.run(workspace, {"action": "list"})

            self.assertEqual(result, "Browsing .: showing 1 entrie(s).\ndocs/")

    def test_list_supports_recursive_and_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("docs/a.txt", "a")
            workspace.write_text("docs/.private.txt", "p")
            workspace.write_text("docs/nested/b.txt", "b")

            result = self.module.run(
                workspace,
                {"action": "browse", "path": "docs", "recursive": True, "include_hidden": True},
            )

            self.assertEqual(
                result,
                "Browsing docs: showing 4 entrie(s).\ndocs/nested/\ndocs/.private.txt\ndocs/a.txt\ndocs/nested/b.txt",
            )

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

    def test_move_copy_delete_and_replace_text_support_bulk_exact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module.run(
                workspace,
                {
                    "action": "write",
                    "paths": ["src/a.txt", "src/b.txt"],
                    "contents": ["TODO one", "TODO two"],
                },
            )
            self.module.run(workspace, {"action": "create_dir", "directories": ["src/nested"]})
            self.module.run(
                workspace,
                {"action": "write", "paths": ["src/nested/c.txt"], "content": "TODO three"},
            )

            move_result = self.module.run(
                workspace,
                {
                    "action": "move",
                    "paths": ["src/a.txt", "src/nested"],
                    "destinations": ["dst/renamed.txt", "dst/tree"],
                },
            )
            copy_result = self.module.run(
                workspace,
                {
                    "action": "copy",
                    "paths": ["src/b.txt"],
                    "destinations": ["dst/copied.txt"],
                },
            )
            replace_result = self.module.run(
                workspace,
                {
                    "action": "replace_text",
                    "paths": ["dst/renamed.txt", "dst/tree/c.txt", "dst/copied.txt"],
                    "find": "TODO",
                    "replace": "DONE",
                },
            )
            delete_result = self.module.run(
                workspace,
                {"action": "delete", "paths": ["src/b.txt", "dst/tree"]},
            )

            self.assertEqual(
                move_result,
                "Moved src/a.txt to dst/renamed.txt.\nMoved src/nested to dst/tree.",
            )
            self.assertEqual(copy_result, "Copied src/b.txt to dst/copied.txt.")
            self.assertEqual(
                replace_result,
                "Replaced 1 occurrence(s) in dst/renamed.txt.\n"
                "Replaced 1 occurrence(s) in dst/tree/c.txt.\n"
                "Replaced 1 occurrence(s) in dst/copied.txt.",
            )
            self.assertEqual(delete_result, "Deleted path: src/b.txt\nDeleted path: dst/tree")
            self.assertEqual(workspace.read_text("dst/renamed.txt"), "DONE one")
            self.assertEqual(workspace.read_text("dst/copied.txt"), "DONE two")
            self.assertFalse(workspace.resolve("dst/tree").exists())

    def test_replace_text_supports_case_insensitive_regex(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module.run(
                workspace,
                {"action": "write", "paths": ["notes/a.txt"], "content": "Old_Value old_value"},
            )

            result = self.module.run(
                workspace,
                {
                    "action": "replace_text",
                    "paths": ["notes/a.txt"],
                    "find": r"old_value",
                    "replace": "new_value",
                    "regex": True,
                    "case_sensitive": False,
                    "max_replacements_per_file": 1,
                },
            )

            self.assertEqual(result, "Replaced 1 occurrence(s) in notes/a.txt.")
            self.assertEqual(workspace.read_text("notes/a.txt"), "new_value old_value")

    def test_workspace_prefix_is_filtered_from_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)

            write_result = self.module.run(
                workspace,
                {
                    "action": "write",
                    "paths": ["workspace/notes/todo.txt", "nested/workspace/notes/next.txt"],
                    "contents": ["hello", "today"],
                },
            )
            read_result = self.module.run(
                workspace,
                {"action": "read", "paths": ["workspace/notes/todo.txt", "nested/workspace/notes/next.txt"]},
            )

            self.assertEqual(
                write_result,
                "Wrote 5 character(s) to notes/todo.txt.\nWrote 5 character(s) to nested/notes/next.txt.",
            )
            self.assertEqual(read_result, "notes/todo.txt:\nhello\n\nnested/notes/next.txt:\ntoday")

    def test_workspace_prefix_is_filtered_from_directories_and_destination(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)

            create_result = self.module.run(
                workspace,
                {"action": "create_dir", "directories": ["workspace/docs", "nested/workspace/archive/2026"]},
            )
            self.assertEqual(
                create_result,
                "Created directory: docs\nCreated directory: nested/archive/2026",
            )

            self.module.run(
                workspace,
                {
                    "action": "write",
                    "paths": ["workspace/docs/a.txt"],
                    "content": "a",
                },
            )

            move_result = self.module.run(
                workspace,
                {
                    "action": "move",
                    "paths": ["workspace/docs/a.txt"],
                    "destination_dir": "nested/workspace/archive/2026",
                },
            )
            self.assertEqual(move_result, "Moved docs/a.txt to nested/archive/2026/a.txt.")

    def test_rejects_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)

            result = self.module.run(
                workspace,
                {"action": "read", "paths": ["../outside.txt"]},
            )

            self.assertEqual(result, "Path escapes workspace: ../outside.txt")


if __name__ == "__main__":
    unittest.main()
