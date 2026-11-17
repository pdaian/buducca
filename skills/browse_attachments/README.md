# Browse attachments skill

## What it does
Discovers files stored under `attachments/`, returning JSON `items` with `path`,
`date`, and `size_bytes`. Existing OCR sidecars appear as `ocr_text_path` rather
than separate attachments. Original file contents are not read.

## Guidance

- Use this when the filename is unknown. Use
  [filename search](../search_attachments_by_filename/README.md) for a known
  substring, or [read_attachment](../read_attachment/README.md) for an exact path.
- `date` filters the storage folder (`YYYY-MM-DD`), not document dates or file
  modification times. Results sort by descending path, not exact arrival time.
- `max_items` defaults to 20; pass a positive integer. There is no pagination
  cursor or total count, so a full result page may omit other files.
- `include_ocr_text` defaults to `false`. Enable it only when existing extracted
  text is needed; this skill does not run OCR. Empty `items` is a valid result.

Uses the Python standard library and the configured workspace.

## Usage

```bash
python3 -m assistant_framework.cli skill browse_attachments --args '{"max_items":20}'
python3 -m assistant_framework.cli skill browse_attachments --args '{"date":"2026-03-10","include_ocr_text":true}'
```

## Args schema

```ts
{
  max_items?: number;
  date?: string;
  include_ocr_text?: boolean;
}
```
