# Web search skill

## What it does
Searches DuckDuckGo and returns result metadata plus cleaned readable text for pages with non-trivial extracted content.

The skill will:
- Check up to `max_pages_checked` search results (default `80`).
- Keep only pages where readable text extraction succeeds with sufficient length/content.
- Stop early once it has collected `min_pages_returned` good pages.
- Return fewer pages if it cannot reach `min_pages_returned` before hitting `max_pages_checked`.

## Dependencies
- Network access.
- Python standard library (`urllib`, `html.parser`, `re`).
- No API key required.

## Usage
```bash
python3 -m assistant_framework.cli skill web_search --args '{"query":"python 3.12 release notes"}'
python3 -m assistant_framework.cli skill web_search --args '{"query":"rust tokio tutorial","max_pages_checked":30,"min_pages_returned":10}'
```

## Args schema
```ts
{
  query: string;
  max_pages_checked?: number;
  min_pages_returned?: number;
}
```
