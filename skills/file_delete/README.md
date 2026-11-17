# File delete skill

## What it does

Permanently deletes one workspace file with `path`, or several with `paths`.
Directories are refused. Use [file](../file/README.md) only when recursive
directory removal is intended.

## Guidance

- Resolve the requested targets from user input or a prior listing. Use paths
  relative to the configured workspace; `paths` takes precedence over `path`.
- There is no trash or undo. A result reports each deletion, missing file, or
  directory refusal separately. Bulk deletion is not transactional; inspect
  affected paths after a failure before deciding whether to retry.
- Paths outside the workspace are rejected. Workspace confinement does not
  make deleting the wrong file recoverable.

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
