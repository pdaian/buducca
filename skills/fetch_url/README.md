# Fetch URL skill

## What it does
Downloads the contents of a specific URL using only Python's standard library.

Supported URL handling comes from `urllib`, which covers common schemes such as:
- `http`
- `https`
- `file`
- `ftp`
- `data`

The skill is read-only. It returns the resolved URL, response status when available, content type, and the body. Text responses are decoded to text. Non-text responses are returned as base64.

## Usage
```bash
python3 -m assistant_framework.cli skill fetch_url --args '{"url":"https://example.com"}'
python3 -m assistant_framework.cli skill fetch_url --args '{"url":"file:///tmp/example.html"}'
python3 -m assistant_framework.cli skill fetch_url --args '{"url":"data:text/plain,hello"}'
```

## Args schema
```ts
{
  url: string;
  timeout_seconds?: number;
  max_bytes?: number;
}
```
