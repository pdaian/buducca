# Search files skill

## What it does
Searches workspace text files with either literal matching or regular expressions.

Use it when you need to search:
- one file via `path`
- several files or directories via `paths`
- an entire directory tree by passing a directory path

Behavior notes:
- `regex` defaults to `false`, so plain text is treated literally unless regex mode is enabled.
- `case_sensitive` defaults to `false`.
- Binary or non-UTF-8 files are skipped.
- Results are returned as `path:line_number:line_text`.
- Search stops after `max_matches` results.

## Usage
```bash
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"TODO","path":"notes/today.txt"}'
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"todo: (first|second)","paths":["notes","docs/todo.txt"],"regex":true,"case_sensitive":true}'
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"error|warning","path":"logs","regex":true,"max_matches":20}'
```

Prefer `path` for a single file or directory. Use `paths` when searching multiple files and/or directories in one call.

## Args schema
```ts
{
  pattern: string;
  path?: string;
  paths?: string[];
  regex?: boolean;
  case_sensitive?: boolean;
  max_matches?: number;
}
```
