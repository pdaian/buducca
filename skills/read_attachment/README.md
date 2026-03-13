# Read attachment skill

Reads a single file under `attachments/`. Text files return their content. Binary files return metadata and optional OCR sidecar text.

## Example

```bash
python3 -m assistant_framework.cli skill read_attachment --args '{"path":"attachments/2026-03-10/note.txt"}'
python3 -m assistant_framework.cli skill read_attachment --args '{"path":"attachments/2026-03-10/scan.pdf","include_ocr_text":true}'
```
