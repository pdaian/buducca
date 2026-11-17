# Search attachments by filename skill

## What it does

Finds saved attachments by a case-insensitive filename substring, optionally
restricted to a `YYYY-MM-DD` storage folder. Returns JSON `items` with `path`,
`date`, and `size_bytes`, excluding `.ocr.txt` sidecars.

## Guidance

- `query` is a non-empty literal substring, not a glob, regex, or content search.
  Stored filenames may include platform, sender, and timestamp prefixes.
- `max_items` defaults to 20; use a positive integer. Results sort by descending
  path, with no total count or pagination cursor. Narrow the query or date when
  the result limit prevents finding the intended file.
- An empty result does not establish that the topic is absent from file contents.
  Use [browse_attachments](../browse_attachments/README.md) if the name is unknown
  and [read_attachment](../read_attachment/README.md) to inspect a returned path.

Uses the Python standard library and the configured workspace.

## Args schema

```ts
{
  query: string;
  date?: string;
  max_items?: number;
}
```

## Usage

```bash
python3 -m assistant_framework.cli skill search_attachments_by_filename --args '{"query":"invoice","max_items":10}'
python3 -m assistant_framework.cli skill search_attachments_by_filename --args '{"query":"scan","date":"2026-03-10"}'
```
