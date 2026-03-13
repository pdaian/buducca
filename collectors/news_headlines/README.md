# news_headlines

Collects up to 100 headlines published in the last 24 hours from a balanced set of public RSS and Atom feeds.

## Why this collector

- Uses only Python standard library HTTP and XML parsing.
- Avoids browser automation, scraping workflows, and API keys by default.
- Spreads picks across multiple publishers instead of taking all results from one feed.

## Generated files

- `news/headlines.last_24h.jsonl`: latest selected headlines for the current run, one JSON object per line.
- `collectors/news_headlines/status.json`: last run status and any source-level fetch errors.
- `collected/raw/news_headlines/<timestamp>.json`: raw per-source payload snapshot for the run.
- `collected/normalized/news_headlines.jsonl`: normalized headline records appended for downstream retrieval.

These files are written to the workspace and are available for discovery and targeted reads. They are not expanded into the model prompt by default.

## Config

Example:

```json
{
  "interval_seconds": 86400,
  "target_count": 100,
  "lookback_hours": 24,
  "sources": [
    {"name": "Associated Press", "url": "https://apnews.com/hub/ap-top-news?output=rss"},
    {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"}
  ]
}
```

## Notes

- The default source list is curated for simple machine-readable access and can be overridden in config.
- If a source fails, the collector still writes output from the sources that succeeded.
