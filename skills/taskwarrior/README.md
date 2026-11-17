# Taskwarrior skill

## What it does

Manages Taskwarrior todos directly through the harness user's `task` CLI: list,
add, modify, and mark done. Results are JSON with an `ok` flag. Use
[`advancetodos`](../advancetodos/README.md) to shift a set of existing due dates by
a fixed duration.

This is the harness user's Taskwarrior database, separate from structured
workspace tasks managed by [memory](../memory/README.md). A due date does not
create a bot reminder; use [add_reminder](../add_reminder/README.md) for delivery.

## Args schema

```ts
{
  action?: "list" | "add" | "modify" | "done";
  filter?: string | string[];
  tasks?: Array<string | number>;
  description?: string;
  project?: string;
  due?: string;
}
```

`action` defaults to `list`. The legacy `command` alias is still accepted; if
both are supplied, they must agree. Unknown arguments and fields inappropriate
for the chosen action produce an error before running Taskwarrior.

| Action | Required arguments | Optional arguments | Result |
| --- | --- | --- | --- |
| `list` | None | `filter` | `tasks`: exported task objects, including UUIDs and any user-defined attributes |
| `add` | Non-empty `description` | `project`, `due` | `output`: Taskwarrior feedback |
| `modify` | Non-empty `tasks`, at least one field to change | `description`, `project`, `due` | `targets`, `output` |
| `done` | Non-empty `tasks` | None | `targets`, `output` |

## Agent guidance

- Read tasks before selecting targets. Prefer full UUIDs from the result;
  numeric IDs can change between commands. `tasks` accepts individual positive
  IDs or full UUIDs, not filters, ID ranges, or arbitrary command arguments.
- Unfiltered `list` exports **all statuses**, including completed/deleted tasks.
  Use `["status:pending"]` for pending todos, `["+OVERDUE"]` for overdue todos,
  or `["(", "status:pending", "or", "status:waiting", ")"]` for unfinished todos
  including waiting tasks. An empty result is a successful `tasks: []`.
- Prefer an array of filter terms. String filters use shell-style quoting to
  separate terms, but no shell is executed. Configuration overrides and `--`
  are not allowed in filters. Use explicit attributes such as
  `description.contains:report` when searching for text.
- Pass descriptions as literal text, without shell escaping or embedded CLI
  modifications. Use the named fields for attributes. `modify` can replace a
  description; omitting a field leaves it alone. Empty `project` or `due`
  clears that field; `null` is invalid.
- `due` accepts Taskwarrior date expressions, such as `tomorrow`, `2026-11-20`,
  or `20261120T150000Z`. Relative dates use the harness timezone and clock.
  A bare duration such as `2d` means **now plus two days**, not the old due date
  plus two days. Use `advancetodos` for the latter.
- Check `ok` before claiming success. Errors have an `error` string. A failed
  write or timeout can have partial effects: list the targets before retrying.
  Successful mutation results include CLI feedback, not an inferred count of
  changes. Read back the tasks when their resulting state matters.

The harness injects `DESCRIPTION` and `ARGS_SCHEMA` into the model prompt;
this README supplies operator help and reference details. Both skills require
a model response after execution so JSON results and errors can be explained.

## Usage

```bash
python3 -m assistant_framework.cli skill taskwarrior --args '{"action":"list","filter":["status:pending","project:Home"]}'
python3 -m assistant_framework.cli skill taskwarrior --args '{"action":"add","description":"Buy milk","project":"Home","due":"tomorrow"}'
python3 -m assistant_framework.cli skill taskwarrior --args '{"action":"modify","tasks":["2e17b750-c137-4ddd-b6dd-c63e272107f4"],"due":""}'
python3 -m assistant_framework.cli skill taskwarrior --args '{"action":"done","tasks":["2e17b750-c137-4ddd-b6dd-c63e272107f4"]}'
```

## Runtime and compatibility

Requires `task` on `PATH` and the Python standard library. The shared adapter in
`assistant_framework/taskwarrior.py` uses CLI JSON export rather than reading
Taskwarrior's storage files. It inherits the harness user's configuration,
including `TASKRC`/`TASKDATA`; the workspace argument does not select a database.
Set up Taskwarrior before running the bot.

Commands use UTF-8, an argument array, closed stdin, and a 30-second timeout.
Per-command overrides force JSON arrays, disable color and bulk prompts, and
clear the active context so selection follows the supplied arguments. Recurring
instance edits do not propagate to siblings (`recurrence.confirmation=no`).
Taskwarrior hooks remain active. No persistent configuration is changed.

Export filters are passed as one parenthesized argument **before** `export`.
Taskwarrior 3.5's [export implementation](https://github.com/GothenburgBitFactory/taskwarrior/blob/v3.5.0/src/commands/CmdExport.cpp)
interprets arguments after `export` as report names: several
trailing filter terms can be ignored, while a single UUID fails report lookup.
Grouping also keeps command names and configured aliases used as search text
from becoming commands. Explicit `limit:`/`limit=` settings remain separate CLI
arguments. This fixes both filtered listing and the UUID readback used by
`advancetodos` without depending on database internals.

The command/JSON boundary avoids dependence on Taskwarrior 2.x text storage or
3.x database internals. Results use JSON:
`{"ok":true,"action":"list","tasks":[...]}`,
`{"ok":true,"action":"modify","targets":[...],"output":"..."}`, or
`{"ok":false,"action":"modify","error":"..."}`. Decode `ok` before interpreting
the action-specific fields; a successful command is not a verified change count.

The interface follows Taskwarrior's [application integration guidance](https://taskwarrior.org/docs/3rd-party/),
[command syntax](https://taskwarrior.org/docs/man/task.1/), and
[automation settings](https://taskwarrior.org/docs/man/taskrc.5/).
