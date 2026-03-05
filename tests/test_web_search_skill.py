import importlib.util
import tempfile
import unittest
from pathlib import Path

from assistant_framework.workspace import Workspace


_SAMPLE_HTML = """
<html>
  <body>
    <div class="results">
      <div class="result">
        <a class="result__a" href="https://example.com/a">Example Result A</a>
        <a class="result__snippet">Snippet A text.</a>
      </div>
      <div class="result">
        <a class="result-link" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fb">Example Result B</a>
        <div class="result-snippet">Snippet B text.</div>
      </div>
    </div>
  </body>
</html>
"""


def load_web_search_module():
    skill_path = Path("skills/web_search.py")
    spec = importlib.util.spec_from_file_location("web_search_skill", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load web_search skill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WebSearchSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_web_search_module()

    def test_missing_query(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            result = self.module.run(workspace, {})
            self.assertEqual(result, "Missing required arg `query`.")

    def test_extracts_and_formats_results(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module._fetch_search_html = lambda query: _SAMPLE_HTML
            self.module._fetch_page_html = lambda url: f"<html><body>Mock page for {url}</body></html>"
            result = self.module.run(workspace, {"query": "unit test", "max_results": 10})

            self.assertIn("DuckDuckGo results for: unit test", result)
            self.assertIn("1. Example Result A", result)
            self.assertIn("URL: https://example.com/a", result)
            self.assertIn("Snippet: Snippet A text.", result)
            self.assertIn("2. Example Result B", result)
            self.assertIn("URL: https://example.com/b", result)
            self.assertIn("Snippet: Snippet B text.", result)
            self.assertIn("HTML:\n<html><body>Mock page for https://example.com/a</body></html>", result)
            self.assertIn("HTML:\n<html><body>Mock page for https://example.com/b</body></html>", result)

    def test_max_results_capped_to_ten(self) -> None:
        links = "\n".join(
            f'<a class="result__a" href="https://example.com/{i}">Result {i}</a><a class="result__snippet">Snippet {i}</a>'
            for i in range(1, 15)
        )
        payload = f"<html><body>{links}</body></html>"

        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module._fetch_search_html = lambda query: payload
            self.module._fetch_page_html = lambda url: "<html></html>"
            result = self.module.run(workspace, {"query": "many", "max_results": 50})
            self.assertIn("10. Result 10", result)
            self.assertNotIn("11. Result 11", result)

    def test_includes_html_fetch_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module._fetch_search_html = lambda query: _SAMPLE_HTML

            def fail(_url):
                raise RuntimeError("boom")

            self.module._fetch_page_html = fail
            result = self.module.run(workspace, {"query": "unit test"})

            self.assertIn("HTML fetch failed: boom", result)


if __name__ == "__main__":
    unittest.main()
