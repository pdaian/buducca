# File skill

## What it does
Provides workspace-safe file and directory operations for browsing and editing:
- `list` or `browse`
- `read`
- `write`
- `append`
- `move`
- `create_dir`
- `delete_dir`

## When the agent should use it
Use this skill for deterministic filesystem work inside the workspace, especially when shell tooling is unavailable or restricted.

Preferred workflow:
1. Use `list` to inspect directories and confirm the exact target paths.
2. Use `read` on the smallest useful set of files.
3. Use `write`, `append`, `move`, or directory actions only after the target paths are clear.

Use `list` instead of guessing filenames. Use `search_files` when you know the text you need but not the path.

## Behavior notes
- All paths are resolved relative to the workspace root.
- Any `workspace/` prefix is stripped from incoming paths.
- `list` defaults to the workspace root when no path is provided.
- `list` excludes hidden files and directories unless `include_hidden` is `true`.
- `list` accepts files or directories. Directory results end with `/`.
- `read` returns `File not found` for missing files.
- `read_line_limit` returns only the last N lines.

## Usage
```bash
python3 -m assistant_framework.cli skill file --args '{"action":"list","path":"skills"}'
python3 -m assistant_framework.cli skill file --args '{"action":"list","path":"docs","recursive":true,"max_entries":50}'
python3 -m assistant_framework.cli skill file --args '{"action":"read","paths":["README.md","docs/developer-guide.md"],"read_line_limit":40}'
python3 -m assistant_framework.cli skill file --args '{"action":"write","paths":["notes/today.txt"],"content":"hello"}'
```

## Args schema
```ts
{
  action: "read" | "list" | "browse" | "write" | "append" | "move" | "create_dir" | "delete_dir";
  path?: string;
  paths?: string[];
  directories?: string[];
  contents?: string[];
  content?: string;
  destination_dir?: string;
  read_line_limit?: number;
  recursive?: boolean;
  include_hidden?: boolean;
  max_entries?: number;
}
```
