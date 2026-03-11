# File delete skill

## What it does
Deletes workspace files in bulk.

## Dependencies
- Python standard library only.
- `assistant_framework.workspace.Workspace` (internal).

## Usage
```bash
python3 -m assistant_framework.cli skill file_delete --args '{"paths":["notes/today.txt","notes/tomorrow.txt"]}'
python3 -m assistant_framework.cli skill file_delete --args '{"path":"reports/latest.csv"}'
```

## Args schema
```ts
{
  paths?: string[];
  path?: string;
}
```
