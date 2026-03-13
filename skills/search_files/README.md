# Search files skill

## What it does
Searches workspace text files with literal matching or regular expressions.

Use it when you know the text pattern you need and want the agent to find the right file paths before opening files.

## When the agent should use it
Use this skill before broad file reads when:
- the filename is unknown
- the same token may appear in many directories
- shell search tools like `rg` are unavailable
- you need a narrow, reproducible search scope inside the workspace

Preferred workflow:
1. Start with a scoped search using `path` or `paths` when possible.
2. Add `file_pattern` to reduce noise by filename or extension.
3. Add `context_lines` when match lines alone are not enough.
4. Follow up with the `file` skill to read the exact matching files.

## Behavior notes
- `regex` defaults to `false`, so plain text is treated literally unless regex mode is enabled.
- `case_sensitive` defaults to `false`.
- Hidden files and directories are skipped unless `include_hidden` is `true`.
- Binary or non-UTF-8 files are skipped.
- Results are returned as `path:line_number:line_text`.
- When `context_lines` is set, surrounding lines are included as `path-line_number-line_text`.
- Search stops after `max_matches` matching lines.
- `file_pattern` accepts a single glob like `*.py` or a list like `["*.md","*.txt"]`.

## Usage
```bash
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"TODO","path":"notes/today.txt"}'
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"def register","paths":["skills"],"file_pattern":"*.py","max_matches":20}'
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"todo: (first|second)","paths":["notes"],"regex":true,"case_sensitive":true}'
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"ActionEnvelope","path":"assistant_framework","file_pattern":["*.py","*.md"],"context_lines":1}'
```

Prefer `path` for one file or directory. Use `paths` for several files and/or directories in one call.

## Args schema
```ts
{
  pattern: string;
  path?: string;
  paths?: string[];
  regex?: boolean;
  case_sensitive?: boolean;
  max_matches?: number;
  context_lines?: number;
  file_pattern?: string | string[];
  include_hidden?: boolean;
}
```
