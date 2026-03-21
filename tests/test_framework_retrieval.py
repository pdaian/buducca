import unittest
from tempfile import TemporaryDirectory

from assistant_framework.retrieval import build_structured_memory_context
from assistant_framework.workspace import Workspace


class RetrievalTests(unittest.TestCase):
    def test_structured_memory_context_is_bounded(self) -> None:
        with TemporaryDirectory() as td:
            workspace = Workspace(td)
            for index in range(5):
                workspace.write_text(
                    f"assistant/facts/fact-{index}.json",
                    (
                        "{\n"
                        f'  "id": "fact-{index}",\n'
                        f'  "statement": "{("x" * 120)}",\n'
                        '  "source": "learn"\n'
                        "}\n"
                    ),
                )

            context = build_structured_memory_context(workspace, line_limit=50, max_files=2, max_chars=420)

            self.assertIn("File: assistant/facts/fact-0.json", context)
            self.assertIn("File: assistant/facts/fact-1.json", context)
            self.assertNotIn("File: assistant/facts/fact-2.json", context)
            self.assertIn("additional learn-sourced file(s) omitted", context)
            self.assertLessEqual(len(context), 500)


if __name__ == "__main__":
    unittest.main()
