# Search files skill

## What it does

Searches UTF-8 workspace file contents with literal matching or regular
expressions. Results identify paths and 1-based line numbers.

Use this skill when you know the text pattern but do not know the exact file path yet. After finding matching paths, switch to the `file` skill to read only the needed lines or perform edits.

## Scope and matching

| Goal | Recommended args |
| --- | --- |
| Search eligible workspace files | `pattern` only |
| Search one directory tree | `pattern`, `path` |
| Search several roots at once | `pattern`, `paths` |
| Limit by extension or name | add `file_pattern` |
| Match exact text literally | keep `regex: false` |
| Use a regex | set `regex: true` |
| Case-sensitive search | set `case_sensitive: true` |
| Show surrounding lines | set `context_lines` |

## Guidance

- Start with known paths or file globs, then broaden if the result is insufficient.
  `paths` takes precedence over `path`. Paths are relative to the configured
  workspace, which is `workspace/` by default, not the repository root.
- Use `context_lines` when the matching line alone lacks context. Read the
  relevant ranges with [file](../file/README.md) before editing.
- A capped result is partial. A negative result applies only to eligible files
  in the selected scope. Attachment names need the attachment search skills;
  attachment text needs an exact file or OCR sidecar read.

## Behavior notes

- `regex` defaults to `false`, so plain text is treated literally unless regex mode is enabled.
- `case_sensitive` defaults to `false`.
- Hidden files and directories are skipped unless `include_hidden` is `true`.
- `attachments/` is always excluded, including when explicitly scoped.
- Files that cannot be decoded as UTF-8 are skipped; there is no separate binary-file detector.
- Results are returned as `path:line_number:line_text`.
- Context lines are returned as `path-line_number-line_text`.
- Search stops after `max_matches` matching lines (default 50, positive integer).
- `context_lines` defaults to 0 and must be a non-negative integer. The match
  line is printed first, followed by context lines; output is not a continuous excerpt.
- `file_pattern` accepts one glob or a list of globs, matched against the
  basename or workspace-relative path. No glob means no filename restriction.

## Usage

```bash
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"TODO"}'
python3 -m assistant_framework.cli skill search_files --workspace . --args '{"pattern":"def register","paths":["skills"],"file_pattern":"*.py","max_matches":20}'
python3 -m assistant_framework.cli skill search_files --args '{"pattern":"todo: (first|second)","paths":["notes"],"regex":true,"case_sensitive":true}'
python3 -m assistant_framework.cli skill search_files --workspace . --args '{"pattern":"ActionEnvelope","path":"assistant_framework","file_pattern":["*.py","*.md"],"context_lines":1}'
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
