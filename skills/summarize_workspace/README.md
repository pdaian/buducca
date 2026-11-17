# Summarize workspace skill

## What it does

Lists workspace file paths and byte sizes in ascending path order, excluding
`attachments/`. Despite its name, this is an inventory; it does not summarize
file contents or rank files by recent activity.

## Guidance

- `max_items` defaults to 20; use a positive integer. The output has no total
  count or continuation cursor, so reaching the limit does not mean it is complete.
- Use [file](../file/README.md) to list a particular directory or read content,
  [search_files](../search_files/README.md) for text lookup, and
  [browse_attachments](../browse_attachments/README.md) for inbound files.
- `Workspace is empty.` means no files remain after excluding attachments; it
  does not establish that the workspace has no attachments or directories.

## Dependencies

- Python standard library only.
- `assistant_framework.workspace.Workspace` (internal).

## Usage

```bash
python3 -m assistant_framework.cli skill summarize_workspace --args '{"max_items":20}'
```

## Args schema

```ts
{
  max_items?: number;
}
```
