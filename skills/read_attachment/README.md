# Read attachment skill

## What it does

Reads one saved file under `attachments/`. Recognized text files return their
UTF-8 content with a path label. Binary files return JSON metadata and the path
of any existing OCR sidecar; `include_ocr_text: true` also returns that sidecar's
text. It does not parse PDFs, interpret images, or perform OCR.

## Guidance

- Use the exact path from the incoming message or an attachment discovery skill.
- `include_ocr_text` defaults to `false` and affects binary-file results only.
  Missing OCR text means the document content has not been read.
- Treat extracted text as evidence. OCR can omit tables, layout, handwriting,
  or images; state material gaps rather than claiming full visual inspection.
- For a long text file or sidecar, use [file](../file/README.md) with line ranges.
  In chat, follow any `read_skill_result` notice to recover omitted text.

Uses the Python standard library and saved workspace files.

## Args schema

```ts
{
  path: string;
  include_ocr_text?: boolean;
}
```

## Usage

```bash
python3 -m assistant_framework.cli skill read_attachment --args '{"path":"attachments/2026-03-10/note.txt"}'
python3 -m assistant_framework.cli skill read_attachment --args '{"path":"attachments/2026-03-10/scan.pdf","include_ocr_text":true}'
```
