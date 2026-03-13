import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


def load_module():
    skill_path = Path("skills/read_attachment/__init__.py")
    spec = importlib.util.spec_from_file_location("read_attachment_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load read_attachment skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReadAttachmentSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_requires_attachment_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.assertEqual(self.module.run(workspace, {}), "Missing required arg `path`.")
            self.assertEqual(
                self.module.run(workspace, {"path": "notes/a.txt"}),
                "`path` must point to a file under attachments/.",
            )

    def test_reads_text_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_text("attachments/2026-03-10/note.txt", "hello")

            result = self.module.run(workspace, {"path": "attachments/2026-03-10/note.txt"})

            self.assertEqual(result, "attachments/2026-03-10/note.txt:\nhello")

    def test_reads_binary_attachment_metadata_and_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            workspace.write_bytes("attachments/2026-03-10/scan.pdf", b"%PDF")
            workspace.write_text("attachments/2026-03-10/scan.pdf.ocr.txt", "scanned text\n")

            result = json.loads(
                self.module.run(
                    workspace,
                    {"path": "attachments/2026-03-10/scan.pdf", "include_ocr_text": True},
                )
            )

            self.assertEqual(
                result,
                {
                    "path": "attachments/2026-03-10/scan.pdf",
                    "size_bytes": 4,
                    "ocr_text_path": "attachments/2026-03-10/scan.pdf.ocr.txt",
                    "ocr_text": "scanned text",
                },
            )


if __name__ == "__main__":
    unittest.main()
