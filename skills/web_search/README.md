# Web search skill

## What it does
Searches DuckDuckGo and returns result metadata plus cleaned readable text for each result page.

## Dependencies
- Network access.
- Python standard library (`urllib`, `html.parser`, `re`).
- No API key required.

## Usage
```bash
python3 -m assistant_framework.cli skill web_search --args '{"query":"python 3.12 release notes"}'
python3 -m assistant_framework.cli skill web_search --args '{"query":"rust tokio tutorial","max_results":5}'
```
