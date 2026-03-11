# Browse attachments skill

## What it does
Returns structured JSON metadata for files stored under `attachments/`.
OCR sidecar files are linked as metadata instead of being treated as standalone attachments.

## Dependencies
- Python standard library only.
- `assistant_framework.workspace.Workspace` (internal).

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
