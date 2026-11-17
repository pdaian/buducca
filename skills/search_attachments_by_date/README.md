# Search attachments by date skill

## What it does

Finds attachments in one `attachments/YYYY-MM-DD/` storage folder. Returns JSON
`items` containing `path`, `date`, and `size_bytes`, excluding `.ocr.txt` sidecars.

## Guidance

- `date` is the storage folder date, not a date inside the document. Resolve a
  relative request such as "yesterday" against the relevant conversation time.
- `max_items` defaults to 20; use a positive integer. Results sort by descending
  path and have no pagination cursor or total count. A full page may be incomplete.
- Empty `items` means no saved files matched that folder. Use
  [read_attachment](../read_attachment/README.md) to inspect a returned file or
  [browse_attachments](../browse_attachments/README.md) to discover other dates.

Uses the Python standard library and the configured workspace.

## Args schema

```ts
{
  date: string;
  max_items?: number;
}
```

## Usage

```bash
python3 -m assistant_framework.cli skill search_attachments_by_date --args '{"date":"2026-03-10","max_items":20}'
```
