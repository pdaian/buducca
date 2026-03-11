import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_browse_attachments_module():
    skill_path = Path("skills/browse_attachments/__init__.py")
    spec = importlib.util.spec_from_file_location("browse_attachments_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load browse_attachments skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrowseAttachmentsSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_browse_attachments_module()

    def test_returns_structured_attachment_items(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("attachments/2026-03-10/a.pdf", "pdf")
            workspace.write_text("attachments/2026-03-10/a.pdf.ocr.txt", "scanned text\n")
            workspace.write_text("attachments/2026-03-09/b.txt", "hello")

            result = json.loads(self.module.run(workspace, {"max_items": 10}))

            self.assertEqual(
                result,
                {
                    "items": [
                        {
                            "path": "attachments/2026-03-10/a.pdf",
                            "date": "2026-03-10",
                            "size_bytes": 3,
                            "ocr_text_path": "attachments/2026-03-10/a.pdf.ocr.txt",
                        },
                        {
                            "path": "attachments/2026-03-09/b.txt",
                            "date": "2026-03-09",
                            "size_bytes": 5,
                        },
                    ]
                },
            )

    def test_filters_by_date_and_can_include_ocr_text(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("attachments/2026-03-10/a.pdf", "pdf")
            workspace.write_text("attachments/2026-03-10/a.pdf.ocr.txt", "scanned text\n")
            workspace.write_text("attachments/2026-03-09/b.txt", "hello")

            result = json.loads(
                self.module.run(
                    workspace,
                    {"date": "2026-03-10", "include_ocr_text": True},
                )
            )

            self.assertEqual(
                result,
                {
                    "items": [
                        {
                            "path": "attachments/2026-03-10/a.pdf",
                            "date": "2026-03-10",
                            "size_bytes": 3,
                            "ocr_text_path": "attachments/2026-03-10/a.pdf.ocr.txt",
                            "ocr_text": "scanned text",
                        }
                    ]
                },
            )


if __name__ == "__main__":
    unittest.main()
