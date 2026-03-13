# Search files skill

## What it does
Searches workspace text files with literal matching or regular expressions.

Use this skill when you know the text pattern but do not know the exact file path yet. After finding matching paths, switch to the `file` skill to read only the needed lines or perform edits.

## Fast decision guide
| Goal | Recommended args |
| --- | --- |
| Search the whole workspace | `pattern` only |
| Search one directory tree | `pattern`, `path` |
| Search several roots at once | `pattern`, `paths` |
| Limit by extension or name | add `file_pattern` |
| Match exact text literally | keep `regex: false` |
| Use a regex | set `regex: true` |
| Case-sensitive search | set `case_sensitive: true` |
| Show surrounding lines | set `context_lines` |

## Rules the agent should follow
1. Start narrow when possible by setting `path` or `paths`.
2. Add `file_pattern` early when you know likely extensions such as `*.py`, `*.md`, or `*.json`.
3. Use `context_lines` only when the match line is not enough.
4. After finding matches, use the `file` skill with `read_mode: "range"` or `"head"` instead of opening entire large files.

## Behavior notes
- `regex` defaults to `false`, so plain text is treated literally unless regex mode is enabled.
- `case_sensitive` defaults to `false`.
- Hidden files and directories are skipped unless `include_hidden` is `true`.
- Binary or non-UTF-8 files are skipped.
- Results are returned as `path:line_number:line_text`.
- Context lines are returned as `path-line_number-line_text`.
- Search stops after `max_matches` matching lines.
- `file_pattern` accepts a single glob or a list of globs.

## Usage
```bash
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"TODO"}'
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"def register","paths":["skills"],"file_pattern":"*.py","max_matches":20}'
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"todo: (first|second)","paths":["notes"],"regex":true,"case_sensitive":true}'
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"ActionEnvelope","path":"assistant_framework","file_pattern":["*.py","*.md"],"context_lines":1}'
```

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
