import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_module():
    skill_path = Path("skills/search_attachments_by_date/__init__.py")
    spec = importlib.util.spec_from_file_location("search_attachments_by_date_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load search_attachments_by_date skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SearchAttachmentsByDateSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_requires_date(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.assertEqual(self.module.run(workspace, {}), "Missing required arg `date`.")

    def test_lists_attachments_for_date(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("attachments/2026-03-10/a.pdf", "pdf")
            workspace.write_text("attachments/2026-03-09/b.txt", "hello")

            result = json.loads(self.module.run(workspace, {"date": "2026-03-10"}))

            self.assertEqual(
                result,
                {
                    "items": [
                        {
                            "path": "attachments/2026-03-10/a.pdf",
                            "date": "2026-03-10",
                            "size_bytes": 3,
                        }
                    ]
                },
            )


if __name__ == "__main__":
    unittest.main()
