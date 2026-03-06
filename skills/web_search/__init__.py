from __future__ import annotations

import html
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from assistant_framework.workspace import Workspace

NAME = "web_search"
REQUIRES_LLM_RESPONSE = True
DESCRIPTION = (
    "Search the web with DuckDuckGo (no API key required). "
    "Args: query (required), max_results (optional, default 10, capped at 10). "
    "Returns title/url/snippet results plus cleaned page text for each linked page."
)

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DEFAULT_MAX_RESULTS = 10
_DEFAULT_MAX_PAGE_CHARS = 2200

_TEXT_BREAK_TAGS = {
    "p",
    "div",
    "li",
    "article",
    "section",
    "main",
    "aside",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
    "tr",
    "td",
    "br",
}

_NON_CONTENT_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "canvas",
    "iframe",
    "template",
    "head",
    "meta",
    "link",
    "object",
    "embed",
}


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._in_anchor = False
        self._anchor_href = ""
        self._anchor_text_parts: list[str] = []

        self._capture_snippet = False
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {k: (v or "") for k, v in attrs}

        if tag == "a":
            href = attrs_map.get("href", "").strip()
            if href:
                self._in_anchor = True
                self._anchor_href = href
                self._anchor_text_parts = []

        class_attr = attrs_map.get("class", "")
        class_tokens = {token.strip().lower() for token in class_attr.split() if token.strip()}
        if "result__snippet" in class_tokens or "result-snippet" in class_tokens:
            self._capture_snippet = True
            self._snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._anchor_text_parts.append(data)
        if self._capture_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_anchor:
            title = _normalize_text("".join(self._anchor_text_parts))
            href = _clean_duckduckgo_href(self._anchor_href)
            if title and href:
                self.results.append({"title": title, "url": href, "snippet": ""})
            self._in_anchor = False
            self._anchor_href = ""
            self._anchor_text_parts = []

        if self._capture_snippet and tag in {"a", "div", "span"}:
            snippet = _normalize_text("".join(self._snippet_parts))
            if snippet:
                self._assign_snippet(snippet)
            self._capture_snippet = False
            self._snippet_parts = []

    def _assign_snippet(self, snippet: str) -> None:
        for item in reversed(self.results):
            if not item.get("snippet"):
                item["snippet"] = snippet
                return


class _ReadableTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buffer: list[str] = []
        self._ignored_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in _NON_CONTENT_TAGS:
            self._ignored_stack.append(tag)
            return
        if not self._ignored_stack and tag in _TEXT_BREAK_TAGS:
            self._flush_buffer()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_stack and self._ignored_stack[-1] == tag:
            self._ignored_stack.pop()
            return
        if not self._ignored_stack and tag in _TEXT_BREAK_TAGS:
            self._flush_buffer()

    def handle_data(self, data: str) -> None:
        if self._ignored_stack:
            return
        if data.strip():
            self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush_buffer()

    def _flush_buffer(self) -> None:
        if not self._buffer:
            return
        text = _normalize_text(" ".join(self._buffer))
        if text:
            self.blocks.append(text)
        self._buffer = []


def _normalize_text(value: str) -> str:
    text = html.unescape(value)
    return " ".join(text.split())


def _clean_duckduckgo_href(href: str) -> str:
    if not href:
        return ""

    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        parsed_local = urlparse(href)
        if parsed_local.path in {"/l/", "/l"}:
            uddg = parse_qs(parsed_local.query).get("uddg", [""])[0]
            href = uddg or href
        else:
            return ""

    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path in {"/l/", "/l"}:
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        href = uddg or href
        parsed = urlparse(href)

    if parsed.scheme not in {"http", "https"}:
        return ""

    return unquote(href)


def _looks_like_code_or_noise(line: str) -> bool:
    if not line:
        return True

    symbols = sum(1 for ch in line if not ch.isalnum() and not ch.isspace())
    letters = sum(1 for ch in line if ch.isalpha())
    digits = sum(1 for ch in line if ch.isdigit())
    non_space = max(1, sum(1 for ch in line if not ch.isspace()))

    symbol_ratio = symbols / non_space
    letter_ratio = letters / non_space

    if symbol_ratio > 0.35 and len(line) > 50:
        return True
    if letter_ratio < 0.45 and len(line) > 50:
        return True
    if re.search(r"[{};]{3,}", line):
        return True
    if re.search(r"function\s*\(|=>|var\s+|const\s+|let\s+", line):
        return True
    if digits > letters * 2 and len(line) > 40:
        return True
    if line.count("<") + line.count(">") > max(6, len(line) // 12):
        return True
    if re.search(r"</?[a-zA-Z][^>]*>", line):
        return True
    return False


def _extract_readable_text(html_payload: str, max_chars: int = _DEFAULT_MAX_PAGE_CHARS) -> str:
    parser = _ReadableTextExtractor()
    parser.feed(html_payload)
    parser.close()

    cleaned_lines: list[str] = []
    seen: set[str] = set()
    current_len = 0
    for block in parser.blocks:
        raw_block = block.strip()
        if len(raw_block) < 30:
            continue
        if "&lt;" in raw_block and "&gt;" in raw_block:
            continue
        line = raw_block
        if re.search(r"</?[^>]+>", line):
            continue
        line = _normalize_text(line)
        if not line:
            continue
        if line in seen:
            continue
        if _looks_like_code_or_noise(line):
            continue

        seen.add(line)
        cleaned_lines.append(line)
        current_len += len(line) + 1
        if current_len >= max_chars:
            break

    if not cleaned_lines:
        return "No readable text extracted from page."
    return "\n".join(cleaned_lines)


def _fetch_search_html(query: str) -> str:
    payload = urlencode({"q": query}).encode("utf-8")
    request = Request(
        _DDG_HTML_URL,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (compatible; buducca-web-search-skill/1.0)",
        },
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _extract_results(html_payload: str, max_results: int) -> list[dict[str, str]]:
    parser = _DuckDuckGoHTMLParser()
    parser.feed(html_payload)

    deduped: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in parser.results:
        url = item.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(item)
        if len(deduped) >= max_results:
            break
    return deduped


def _fetch_page_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; buducca-web-search-skill/1.0)",
        },
        method="GET",
    )
    with urlopen(request, timeout=15) as response:
        content_type = response.headers.get("Content-Type", "").lower()
        if content_type and not (
            content_type.startswith("text/")
            or "html" in content_type
            or "xml" in content_type
            or "json" in content_type
        ):
            return f"Unsupported page content type: {content_type}"
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def run(workspace: Workspace, args: dict[str, Any]) -> str:
    del workspace

    query = str(args.get("query", "")).strip()
    if not query:
        return "Missing required arg `query`."

    try:
        max_results = int(args.get("max_results", _DEFAULT_MAX_RESULTS))
    except (TypeError, ValueError):
        max_results = _DEFAULT_MAX_RESULTS

    max_results = max(1, min(_DEFAULT_MAX_RESULTS, max_results))

    try:
        payload = _fetch_search_html(query)
    except Exception as exc:
        return f"Web search failed: {exc}"

    results = _extract_results(payload, max_results=max_results)
    if not results:
        return f"No results found for query: {query}"

    lines = [f"DuckDuckGo results for: {query}"]
    for idx, item in enumerate(results, start=1):
        lines.append(f"{idx}. {item['title']}")
        lines.append(f"   URL: {item['url']}")
        snippet = item.get("snippet", "")
        if snippet:
            lines.append(f"   Snippet: {snippet}")

        try:
            page_html = _fetch_page_html(item["url"])
            lines.append("   Page text:")
            lines.append(_extract_readable_text(page_html))
        except Exception as exc:
            lines.append(f"   Page fetch failed: {exc}")

    return "\n".join(lines)
