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

_SAMPLE_PAGE_HTML = """
<html>
  <head>
    <script>const huge = {"blob": "aaaaaaaaaaaaaaaaaaaa"}; function x(){return 1;}</script>
    <style>body { background: #fff; }</style>
  </head>
  <body>
    <article>
      <h1>A clear title for the article</h1>
      <p>This paragraph contains human-readable text about the subject and should be kept.</p>
      <p>const config = { token: "abc" } ;;;;;;</p>
      <p>Another readable paragraph that provides useful context from the page body for summarization.</p>
    </article>
  </body>
</html>
"""

_ESCAPED_HTML_TEXT_PAGE = """
<html>
  <body>
    <p>&lt;div class=\"shell\"&gt;rm -rf /&lt;/div&gt; &lt;span&gt;literal markup text&lt;/span&gt;</p>
    <p>This is a normal sentence with enough words to be useful for the assistant response.</p>
  </body>
</html>
"""

_YOUTUBE_SAMPLE_HTML = """
<html><head><script>
var ytInitialData = {"contents":{"twoColumnSearchResultsRenderer":{"primaryContents":{"sectionListRenderer":{"contents":[{"itemSectionRenderer":{"contents":[
  {"videoRenderer":{"videoId":"abc123","title":{"runs":[{"text":"Video A title"}]},"detailedMetadataSnippets":[{"snippetText":{"runs":[{"text":"Video A snippet"}]}}]}},
  {"videoRenderer":{"videoId":"def456","title":{"runs":[{"text":"Video B title"}]},"detailedMetadataSnippets":[{"snippetText":{"runs":[{"text":"Video B snippet"}]}}]}}
]}}]}}}}};
</script></head><body></body></html>
"""


def load_web_search_module():
    skill_path = Path("skills/web_search/__init__.py")
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
            self.module._fetch_page_html = lambda url: f"<html><body><p>Mock page text for {url} with enough readable words for output and additional context to clearly exceed the non trivial threshold required by the skill implementation for acceptance.</p></body></html>"
            result = self.module.run(workspace, {"query": "unit test", "max_pages_checked": 10, "min_pages_returned": 2})

            self.assertIn("DuckDuckGo results for: unit test", result)
            self.assertIn("1. Example Result A", result)
            self.assertIn("URL: https://example.com/a", result)
            self.assertIn("Snippet: Snippet A text.", result)
            self.assertIn("2. Example Result B", result)
            self.assertIn("Returned 2 page(s) with non-trivial text", result)
            self.assertIn("Source links checked:", result)
            self.assertIn("Pages with extracted non-trivial text:", result)
            self.assertIn("URL: https://example.com/b", result)
            self.assertIn("Snippet: Snippet B text.", result)
            self.assertIn("Page text:\nMock page text for https://example.com/a", result)
            self.assertIn("Page text:\nMock page text for https://example.com/b", result)

    def test_respects_max_pages_checked_limit(self) -> None:
        links = "\n".join(
            f'<a class="result__a" href="https://example.com/{i}">Result {i}</a><a class="result__snippet">Snippet {i}</a>'
            for i in range(1, 15)
        )
        payload = f"<html><body>{links}</body></html>"

        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module._fetch_search_html = lambda query: payload
            self.module._fetch_page_html = lambda url: "<html><body><p>This generated page includes a descriptive paragraph with enough words to pass the non trivial content check and should therefore be included in the final output for each checked result page.</p></body></html>"
            result = self.module.run(workspace, {"query": "many", "max_pages_checked": 3, "min_pages_returned": 10})
            self.assertIn("Returned 3 page(s) with non-trivial text", result)
            self.assertIn("3. Result 3", result)
            self.assertNotIn("4. Result 4", result)

    def test_skips_pages_that_fail_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module._fetch_search_html = lambda query: _SAMPLE_HTML

            def fetch(url):
                if url.endswith("/a"):
                    raise RuntimeError("boom")
                return "<html><body><p>Fallback page text that is long enough to satisfy the non trivial threshold after one failed fetch attempt so at least one page can be returned, including extra explanatory wording about the topic, methodology, caveats, and practical outcomes to ensure the extracted text is comfortably above the minimum length gate.</p></body></html>"

            self.module._fetch_page_html = fetch
            result = self.module.run(workspace, {"query": "unit test", "min_pages_returned": 2, "max_pages_checked": 3})

            self.assertIn("Returned 1 page(s) with non-trivial text", result)
            self.assertIn("1. Example Result B", result)

    def test_defaults_to_checking_eighty_pages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module._fetch_search_html = lambda query: "<html></html>"

            observed = {}

            def fake_extract_results(_payload, max_results):
                observed["max_results"] = max_results
                return []

            self.module._extract_results = fake_extract_results
            result = self.module.run(workspace, {"query": "defaults"})

            self.assertEqual(observed.get("max_results"), 80)
            self.assertEqual(result, "No results found for query: defaults")

    def test_returns_checked_links_even_when_no_page_has_non_trivial_text(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module._fetch_search_html = lambda query: _SAMPLE_HTML
            self.module._fetch_page_html = lambda url: "<html><body><p>Short text</p></body></html>"

            result = self.module.run(workspace, {"query": "thin content", "max_pages_checked": 2})

            self.assertIn("Checked 2 page(s), but none had non-trivial readable text.", result)
            self.assertIn("Source links checked:", result)
            self.assertIn("1. Example Result A", result)
            self.assertIn("URL: https://example.com/a", result)
            self.assertIn("2. Example Result B", result)
            self.assertIn("URL: https://example.com/b", result)

    def test_extract_readable_text_filters_scripts_and_noise(self) -> None:
        text = self.module._extract_readable_text(_SAMPLE_PAGE_HTML)
        self.assertIn("This paragraph contains human-readable text about the subject and should be kept.", text)
        self.assertIn("Another readable paragraph that provides useful context from the page body for summarization.", text)
        self.assertNotIn("function x", text)
        self.assertNotIn("const config", text)

    def test_extract_readable_text_drops_escaped_markup_lines(self) -> None:
        text = self.module._extract_readable_text(_ESCAPED_HTML_TEXT_PAGE)
        self.assertIn("This is a normal sentence with enough words to be useful for the assistant response.", text)
        self.assertNotIn("<div", text)
        self.assertNotIn("literal markup text", text)

    def test_is_non_trivial_text(self) -> None:
        self.assertFalse(self.module._is_non_trivial_text("No readable text extracted from page."))
        self.assertFalse(self.module._is_non_trivial_text("Too short"))
        self.assertTrue(
            self.module._is_non_trivial_text(
                "This is a sufficiently long sample paragraph with many words that should definitely pass the non trivial content gate used by the web search skill before returning parsed page text to the user."
            )
        )

    def test_video_mode_returns_youtube_results(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module._fetch_youtube_search_html = lambda query: _YOUTUBE_SAMPLE_HTML

            result = self.module.run(workspace, {"query": "python async", "mode": "video", "max_video_results": 2})

            self.assertIn("YouTube video results for: python async", result)
            self.assertIn("Returned 2 video result(s).", result)
            self.assertIn("1. Video A title", result)
            self.assertIn("URL: https://www.youtube.com/watch?v=abc123", result)
            self.assertIn("Snippet: Video A snippet", result)
            self.assertIn("2. Video B title", result)
            self.assertIn("URL: https://www.youtube.com/watch?v=def456", result)

    def test_video_mode_handles_empty_results(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Workspace(td)
            self.module._fetch_youtube_search_html = lambda query: "<html><body>No payload</body></html>"

            result = self.module.run(workspace, {"query": "no videos", "mode": "video"})

            self.assertEqual(result, "No YouTube videos found for query: no videos")


if __name__ == "__main__":
    unittest.main()
