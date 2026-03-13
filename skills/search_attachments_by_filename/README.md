# Search attachments by filename skill

Searches files stored under `attachments/` by filename, with an optional date filter.

## Example

```bash
python3 -m assistant_framework.cli skill search_attachments_by_filename --args '{"query":"invoice","max_items":10}'
python3 -m assistant_framework.cli skill search_attachments_by_filename --args '{"query":"scan","date":"2026-03-10"}'
```
