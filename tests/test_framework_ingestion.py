import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from assistant_framework.ingestion import ingest_attachment


class IngestionTests(unittest.TestCase):
    def test_pdf_ocr_fallback_uses_local_tools_when_text_extraction_is_empty(self) -> None:
        with TemporaryDirectory() as td:
            pdf_path = Path(td) / "scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4")
            page_path = Path(td) / "page-1.png"
            page_path.write_bytes(b"png")

            fake_reader = Mock()
            fake_reader.pages = [Mock(extract_text=Mock(return_value=""))]

            with patch("assistant_framework.ingestion.which", side_effect=lambda name: f"/usr/bin/{name}"):
                with patch("assistant_framework.ingestion.tempfile.TemporaryDirectory") as tempdir:
                    tempdir.return_value.__enter__.return_value = td
                    tempdir.return_value.__exit__.return_value = False
                    with patch("assistant_framework.ingestion.subprocess.run") as run:
                        run.side_effect = [
                            Mock(returncode=0, stdout="", stderr=""),
                            Mock(returncode=0, stdout="OCR text", stderr=""),
                        ]
                        with patch.dict("sys.modules", {"pypdf": Mock(PdfReader=Mock(return_value=fake_reader))}):
                            ingested = ingest_attachment(pdf_path)

            self.assertEqual(ingested["text"], "OCR text")
            self.assertEqual(ingested["metadata"]["ocr"], "tesseract")
