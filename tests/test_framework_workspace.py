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


if __name__ == "__main__":
    unittest.main()
