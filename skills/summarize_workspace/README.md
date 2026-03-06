# Summarize workspace skill

## What it does
Lists workspace files and sizes, up to `max_items`.

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
