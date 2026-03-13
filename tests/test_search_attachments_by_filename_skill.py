import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_module():
    skill_path = Path("skills/search_attachments_by_filename/__init__.py")
    spec = importlib.util.spec_from_file_location("search_attachments_by_filename_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load search_attachments_by_filename skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SearchAttachmentsByFilenameSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_requires_query(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.assertEqual(self.module.run(workspace, {}), "Missing required arg `query`.")

    def test_searches_attachment_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("attachments/2026-03-10/invoice.pdf", "pdf")
            workspace.write_text("attachments/2026-03-09/notes.txt", "hello")

            result = json.loads(self.module.run(workspace, {"query": "invoice"}))

            self.assertEqual(
                result,
                {
                    "items": [
                        {
                            "path": "attachments/2026-03-10/invoice.pdf",
                            "date": "2026-03-10",
                            "size_bytes": 3,
                        }
                    ]
                },
            )

    def test_can_filter_by_date(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("attachments/2026-03-10/scan.pdf", "pdf")
            workspace.write_text("attachments/2026-03-09/scan-old.pdf", "pdf")

            result = json.loads(self.module.run(workspace, {"query": "scan", "date": "2026-03-10"}))

            self.assertEqual(len(result["items"]), 1)
            self.assertEqual(result["items"][0]["path"], "attachments/2026-03-10/scan.pdf")


if __name__ == "__main__":
    unittest.main()
