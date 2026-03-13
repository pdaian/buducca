# File skill

## What it does
Provides workspace-safe file and directory operations for deterministic filesystem work.

Supported actions:
- `list` or `browse`
- `read`
- `write`
- `append`
- `move`
- `copy`
- `delete`
- `replace_text`
- `create_dir`
- `delete_dir`

This skill is the primary file-management skill. Use it for exact path operations after you know the target paths. Use `search_files` first when you know text but do not know the path.

## Fast decision guide
Use this table literally.

| Goal | Action | Required args | Notes |
| --- | --- | --- | --- |
| See files and directories | `list` | none, or `path` / `paths` | If omitted, it lists the workspace root. |
| Read whole files | `read` | `path` or `paths` | Use for exact file inspection. |
| Read only the first N lines | `read` | `path` or `paths`, `read_mode: "head"`, `read_line_limit` | Best for large files. |
| Read only the last N lines | `read` | `path` or `paths`, `read_mode: "tail"`, `read_line_limit` | Default when `read_line_limit` is given without `read_mode`. |
| Read a line range | `read` | `path` or `paths`, `read_mode: "range"`, `start_line` and/or `end_line` | Lines are 1-based and inclusive. |
| Create or overwrite files | `write` | `paths`, plus `contents` or shared `content` | Creates parent directories automatically. |
| Append to files | `append` | `paths`, plus `contents` or shared `content` | Does not add a newline unless provided. |
| Rename or move files/directories to exact paths | `move` | `paths`, `destinations` | Use for refactors and renames. |
| Move many items into one directory | `move` | `paths`, `destination_dir` | Keeps each original basename. |
| Duplicate files/directories | `copy` | `paths`, `destinations` or `destination_dir` | Copies directories recursively. |
| Delete files or directories | `delete` | `paths` | Safe workspace-only deletion. |
| Create directories | `create_dir` | `directories` | Creates parent directories automatically. |
| Delete directories only | `delete_dir` | `directories` | Use when you specifically know the target is a directory. |
| Replace text in many files | `replace_text` | `paths`, `find` | Supports literal or regex replacement. |

## Rules the agent should follow
1. Never guess paths if `list` or `search_files` can confirm them first.
2. Prefer the smallest useful read:
   use `read_mode: "head"`, `"tail"`, or `"range"` instead of reading a full large file.
3. For refactors, prefer `move` with `destinations` when the final path is known exactly.
4. For broad content edits, prefer `replace_text` over rewriting whole files.
5. For bulk operations, pass all targets in one call when they share the same action.
6. All paths are relative to the workspace root. Any `workspace/` prefix is stripped automatically.
7. Paths outside the workspace are rejected.

## Behavior notes
- `list` accepts a file or directory. Directory results end with `/`.
- `list` excludes hidden files and directories unless `include_hidden` is `true`.
- `list` returns entries sorted with directories first, then files.
- `read` returns `File not found` for missing files.
- `read_mode: "full"` returns the entire file.
- `read_mode: "head"` returns the first `read_line_limit` lines.
- `read_mode: "tail"` returns the last `read_line_limit` lines.
- `read_mode: "range"` returns an inclusive 1-based slice.
- If `read_line_limit` is provided without `read_mode`, the skill behaves like `tail`.
- `move` and `copy` accept exactly one of `destination_dir` or `destinations`.
- With `destination_dir`, each item keeps its basename.
- `copy` copies directories recursively.
- `delete` removes files or directories.
- `replace_text` defaults to literal, case-sensitive replacement.
- `replace_text` can use regex with `regex: true`.
- `replace_text` writes the file back even if zero matches are found; the result will report `0 occurrence(s)`.

## Recommended workflows
### Inspect then edit
1. `list` the likely directory.
2. `read` only the needed lines.
3. `write`, `append`, or `replace_text` once the target is confirmed.

### Search then patch
1. Use `search_files` to find candidate paths.
2. Use `read` with `read_mode: "range"` to inspect the exact area.
3. Use `replace_text` for simple repeated edits, or `write` for full replacement.

### Refactor files and directories
1. `list` the source and destination parents.
2. Use `move` with `destinations` for exact renames.
3. Use `copy` if you need duplication instead of relocation.
4. Use `delete` only after verifying the new paths exist if the operation is destructive.

## Usage
```bash
python3 -m assistant_framework.cli skill file --args '{"action":"list"}'
python3 -m assistant_framework.cli skill file --args '{"action":"list","path":"skills","recursive":true,"max_entries":50}'
python3 -m assistant_framework.cli skill file --args '{"action":"read","paths":["README.md"],"read_mode":"head","read_line_limit":40}'
python3 -m assistant_framework.cli skill file --args '{"action":"read","path":"assistant_framework/workspace.py","read_mode":"range","start_line":20,"end_line":60}'
python3 -m assistant_framework.cli skill file --args '{"action":"write","paths":["notes/today.txt"],"content":"hello"}'
python3 -m assistant_framework.cli skill file --args '{"action":"append","paths":["notes/today.txt"],"content":"\nnext"}'
python3 -m assistant_framework.cli skill file --args '{"action":"move","paths":["docs/a.txt","docs/b.txt"],"destination_dir":"archive/2026"}'
python3 -m assistant_framework.cli skill file --args '{"action":"move","paths":["src/old.py","src/legacy"],"destinations":["src/new.py","src/archive/legacy"]}'
python3 -m assistant_framework.cli skill file --args '{"action":"copy","paths":["docs/template.md"],"destinations":["docs/template-copy.md"]}'
python3 -m assistant_framework.cli skill file --args '{"action":"replace_text","paths":["a.txt","b.txt"],"find":"TODO","replace":"DONE"}'
python3 -m assistant_framework.cli skill file --args '{"action":"replace_text","paths":["src/app.py"],"find":"old_(name|path)","replace":"new_\\1","regex":true}'
```

## Args schema
```ts
{
  action: "read" | "list" | "browse" | "write" | "append" | "move" | "copy" | "delete" | "replace_text" | "create_dir" | "delete_dir";
  path?: string;
  paths?: string[];
  directories?: string[];
  contents?: string[];
  content?: string;
  destination_dir?: string;
  destinations?: string[];
  read_mode?: "full" | "head" | "tail" | "range";
  read_line_limit?: number;
  start_line?: number;
  end_line?: number;
  recursive?: boolean;
  include_hidden?: boolean;
  max_entries?: number;
  find?: string;
  replace?: string;
  regex?: boolean;
  case_sensitive?: boolean;
  max_replacements_per_file?: number;
}
```
