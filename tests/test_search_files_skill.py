import importlib.util
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_search_files_module():
    skill_path = Path("skills/search_files/__init__.py")
    spec = importlib.util.spec_from_file_location("search_files_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load search_files skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SearchFilesSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_search_files_module()

    def test_requires_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {})
            self.assertEqual(result, "Missing required arg `pattern`.")

    def test_searches_entire_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("notes/a.txt", "alpha\nneedle here\nomega\n")
            workspace.write_text("logs/b.txt", "Needle upper\n")

            result = self.module.run(workspace, {"pattern": "needle"})

            self.assertIn("Found 2 match(es) for `needle` across 2 file(s).", result)
            self.assertIn("notes/a.txt:2:needle here", result)
            self.assertIn("logs/b.txt:1:Needle upper", result)

    def test_searches_specific_paths_with_regex(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("notes/a.txt", "todo: first\nnote\n")
            workspace.write_text("notes/b.txt", "todo: second\n")
            workspace.write_text("archive/c.txt", "todo: hidden\n")

            result = self.module.run(
                workspace,
                {"pattern": r"todo: (first|second)", "paths": ["notes"], "regex": True, "case_sensitive": True},
            )

            self.assertIn("Found 2 match(es) for `todo: (first|second)` across 2 file(s).", result)
            self.assertIn("notes/a.txt:1:todo: first", result)
            self.assertIn("notes/b.txt:1:todo: second", result)
            self.assertNotIn("archive/c.txt", result)

    def test_reports_no_match_for_scoped_search(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("notes/a.txt", "alpha\nbeta\n")

            result = self.module.run(workspace, {"pattern": "needle", "paths": ["notes/a.txt"]})

            self.assertEqual(result, "No matches for `needle` in notes/a.txt.")

    def test_parses_string_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("notes/a.txt", "Needle\n")

            result = self.module.run(
                workspace,
                {"pattern": "needle", "paths": ["notes/a.txt"], "case_sensitive": "false", "regex": "false"},
            )

            self.assertIn("notes/a.txt:1:Needle", result)


if __name__ == "__main__":
    unittest.main()
