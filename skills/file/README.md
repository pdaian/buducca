# File skill

## What it does

Lists, reads, and edits files and directories in the configured workspace.
Use exact known paths; use [search_files](../search_files/README.md) to locate
text first when the path is unknown. Attachment discovery uses
[browse_attachments](../browse_attachments/README.md).

`action` defaults to `read`; legacy `command` is accepted when `action` is absent.
`path` selects one item; `paths` selects multiple and takes precedence over `path`.
Supply workspace-relative paths without an extra `workspace/` prefix. The CLI
defaults to the `workspace/` directory; use `--workspace .` for repository files.
Legacy path normalization is conditional and should not be relied on for new calls.

## Actions

| Goal | Action | Required args | Notes |
| --- | --- | --- | --- |
| See files and directories | `list` | none, or `path` / `paths` | If omitted, it lists the workspace root. |
| Read whole files | `read` | `path` or `paths` | Appropriate when the full content is needed and fits the result cap. |
| Read only the first N lines | `read` | `path` or `paths`, `read_mode: "head"`, `read_line_limit` | Best for large files. |
| Read only the last N lines | `read` | `path` or `paths`, `read_mode: "tail"`, `read_line_limit` | Default when `read_line_limit` is given without `read_mode`. |
| Read a line range | `read` | `path` or `paths`, `read_mode: "range"`, `start_line` and/or `end_line` | Lines are 1-based and inclusive. |
| Create or overwrite files | `write` | `path` / `paths`, plus `contents` or shared `content` | Creates parent directories; replaces existing content. |
| Append to files | `append` | `path` / `paths`, plus `contents` or shared `content` | Does not add a newline unless provided. |
| Rename or move files/directories to exact paths | `move` | `paths`, `destinations` | Use for refactors and renames. |
| Move many items into one directory | `move` | `paths`, `destination_dir` | Keeps each original basename. |
| Duplicate files/directories | `copy` | `paths`, `destinations` or `destination_dir` | Copies directories recursively. |
| Delete files or directories | `delete` | `path` or `paths` | Permanently removes directories recursively; no trash or rollback. |
| Create directories | `create_dir` | `directories` | Creates parent directories automatically. |
| Delete directories only | `delete_dir` | `directories` | Recursively deletes contents; refuses files. |
| Replace text in many files | `replace_text` | `paths`, `find` | Supports literal or regex replacement. |

## Behavior notes

- `list` accepts a file or directory. Directory results end with `/`.
- `list` excludes hidden files and directories unless `include_hidden` is `true`.
- Directory listings omit `attachments/`; use the attachment discovery skills.
- `list` returns entries sorted with directories first, then files.
- `recursive` defaults to `false`; `max_entries` defaults to 200 per call and
  must be a positive integer. A limited listing is not an exhaustive inventory.
- `read` returns `File not found` for missing files.
- `read_mode: "full"` returns the entire file to the caller. The bot may show the model a bounded preview with recovery instructions.
- `read_mode: "head"` returns the first `read_line_limit` lines.
- `read_mode: "tail"` returns the last `read_line_limit` lines.
- `read_mode: "range"` returns an inclusive 1-based slice.
- Without `read_mode`, a start/end line selects `range`; otherwise a line limit
  selects `tail`, and no read options selects `full`. Head/tail require a positive
  line limit. A range needs at least one endpoint; without an end, its limit bounds
  the range if supplied, otherwise it continues to EOF.
- `move` and `copy` accept exactly one of `destination_dir` or `destinations`.
- `contents` and `destinations` must align one-for-one with `paths`. Shared
  `content` applies to each file; `destination_dir` preserves each source basename.
- `copy` copies directories recursively and refuses an existing directory
  destination. File destinations can be overwritten; inspect them when that matters.
- `delete` and `delete_dir` also report success for already-absent paths. Use
  [file_delete](../file_delete/README.md) when targets must be files only.
- `replace_text` defaults to literal, case-sensitive replacement.
- `regex: true` enables Python regular expressions in `find`. Replacement text
  follows [Python `re.sub` syntax](https://docs.python.org/3/library/re.html#re.sub)
  even with literal matching: backslashes and group references need appropriate
  escaping. Omitting `replace` removes matches.
- `max_replacements_per_file` defaults to 0 (all matches); set 1 for a single
  intended occurrence and inspect the result. This is a cap, not an expected-count check.
- `replace_text` writes the file back even if zero matches are found; the result will report `0 occurrence(s)`.
- Bulk mutations run sequentially without rollback. After a failure, inspect
  affected paths before retrying, especially for append, move, or delete.
- Paths resolving outside the workspace are rejected. This boundary does not
  protect against an unintended overwrite or deletion inside the workspace.

## Reading across multiple calls

The bot exposes `runtime.max_skill_result_chars` as the per-result character cap
(4,000 by default, shared across all paths in one read). This is not the model's
total token window or its remaining capacity. Earlier results, instructions,
conversation history, and the final answer also consume context. When the cap is
`null`, reads are unbounded, so still prefer the smallest useful excerpts.

1. Confirm paths with `list` or `search_files`, then start with a small `head`
   or `range` read for an unfamiliar file. Set `done: false` in the enclosing
   `skill_call` so the result comes back for inspection and further calls.
2. Track which paths and ranges have been inspected. For example, after fully
   reading lines 1–40, request `read_mode: "range", start_line: 41, end_line: 80`.
   Lines are 1-based and inclusive; long lines can exceed the character cap even
   with a small line limit. An omitted `read_mode` with `read_line_limit` means
   `tail`, so specify `range` when moving forward.
3. If a result is truncated, follow its `read_skill_result` notice or narrow the
   file range before moving past unread content. Keep the original `step` while
   paging that stored result, and use each returned `next offset`. These offsets
   are zero-based characters in the complete result, including path labels and
   separators, not line numbers or offsets within one file. The end offset is
   exclusive; stop when it reaches the total length. Do not reread the same full
   file to recover an omitted middle section.
4. Follow the current cap and remaining-call advice returned by the bot. A
   context-overflow recovery can lower the cap during the turn; recovery reads
   also consume skill-chain steps. Stored results are only available in the
   current turn. Keep concise findings and read only evidence still needed.
5. Stop when the task is answered or no calls remain. Disclose relevant unread
   portions rather than claiming complete coverage. For an edit, use
   `replace_text` when suitable; do not overwrite a whole file from a partial read.

The raw CLI skill returns full requested content without the bot's cap or
`read_skill_result` recovery tool. Use explicit line ranges there for large reads.

## Usage

```bash
python3 -m assistant_framework.cli skill file --args '{"action":"list"}'
python3 -m assistant_framework.cli skill file --workspace . --args '{"action":"list","path":"skills","recursive":true,"max_entries":50}'
python3 -m assistant_framework.cli skill file --workspace . --args '{"action":"read","paths":["README.md"],"read_mode":"head","read_line_limit":40}'
python3 -m assistant_framework.cli skill file --workspace . --args '{"action":"read","path":"assistant_framework/workspace.py","read_mode":"range","start_line":20,"end_line":60}'
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
  action?: "read" | "list" | "browse" | "write" | "append" | "move" | "copy" | "delete" | "replace_text" | "create_dir" | "delete_dir";
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
