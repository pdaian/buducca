# Advance todos skill

## What it does
Shifts each selected Taskwarrior todo's due date **by a fixed amount**, added
to its existing due date. Positive amounts move dates later; negative amounts
bring them earlier. Without a date range, selects all unfinished tasks with due
dates, including future tasks. With both dates, selects unfinished tasks due
within that inclusive range. Calls the harness user's `task` CLI directly.

## Args schema
```ts
{
  amount: string;
  start_date?: string;
  end_date?: string;
  dry_run?: boolean;
}
```

- `amount` is required and nonzero. Accepts seconds (`s`, `sec`, `seconds`),
  minutes (`min`, `minutes`), hours (`h`, `hours`), days (`d`, `days`), and weeks
  (`w`, `weeks`), including compounds such as `1d 2h 30min`. Fixed ISO durations
  such as `P2D`, `P1W`, and `PT90M` also work. An optional leading `+` or `-` applies
  to the entire duration: `-1h 30min` and `-PT90M` both move dates 90 minutes
  earlier. Fractions are allowed when the total is a whole number of seconds,
  e.g. `1.5h` or `-1.5h`. Bare numbers, zero, signs on individual components,
  months, years, and ambiguous `m` units are rejected.
- Omit both dates to select all pending and waiting tasks with due dates,
  whether overdue, due today, or due in the future. The amount is added once to
  each original due date, preserving the spacing between tasks.
- Supply **both** `start_date` and `end_date` as `YYYY-MM-DD` to restrict
  selection to an inclusive range of due dates in the harness's local
  timezone. Same-day ranges work. Invalid, incomplete, or reversed ranges fail
  before reading or modifying tasks.
- `dry_run` defaults to `false`. Set it to `true` to return a proposed change
  list without issuing writes. A later apply call selects tasks afresh; a preview
  does not reserve or lock them.

Days are exactly 24 hours and weeks exactly seven days. Across a daylight-saving
transition, the local clock hour may change. This is elapsed-time arithmetic,
not calendar-month or local-wall-clock arithmetic. Returned due timestamps use
Taskwarrior's UTC format, `YYYYMMDDTHHMMSSZ`.

## Selection and execution
Selects pending and waiting tasks that have a due date. Completed/deleted tasks,
recurring templates, and tasks without a due date are excluded. Existing
recurring instances can be advanced individually; the recurring schedule and
sibling instances are not shifted by propagation. Only `due` is explicitly
modified; Taskwarrior may update bookkeeping such as `modified`, and configured
hooks still run. `scheduled`, `wait`, descriptions, tags, projects, and user-defined
attributes are not sent as modifications.

All proposed changes are calculated and validated before the first write. Each
write targets a full UUID and requires the original due date and an unfinished
status to still match. The skill reads back the task to verify its new due date.
Updates are sequential, not a transaction: it stops at the first command or
verification failure and reports partial progress without rolling anything back.

The shared [Taskwarrior adapter](../taskwarrior/README.md#runtime-and-compatibility)
uses the harness user's database and timezone, clears any active context for
these commands, disables interactive prompts and recurrence propagation, and
limits each CLI call to 30 seconds. No task files, database files, or persistent
Taskwarrior settings are edited directly. Requires `task` on `PATH` and the
Python standard library; no extra installation or skill registration is needed.

## Results and retries
Results are JSON with `ok`, `dry_run`, `scope` (`all` or `date_range`),
signed `amount_seconds`, `matched`, `updated_count`, and `updated`. Each change includes
`uuid`, `description`, `old_due`, and `new_due`. A preview adds `changes` and has
zero updates. No matches is a successful result with zero counts and no writes.

Input/export/preflight errors return `ok:false`, `error`, and `updated_count:0`.
An update failure also reports `failed` with the attempted old/new dates and an
`outcome` of `unknown`, plus `unattempted_count`. Only verified updates appear
in `updated`; the failed task may have changed even if verification failed.

**Do not blindly repeat a failed apply call.** A second call can advance already
updated tasks again. Inspect the reported UUIDs with `taskwarrior` and use
explicit absolute due dates to resolve uncertain outcomes. The skill is not
idempotent and does not store a retry ledger.

## Usage
```bash
# Move all dated unfinished todos two days later.
python3 -m assistant_framework.cli skill advancetodos --args '{"amount":"2d"}'

# Bring all dated unfinished todos two days earlier.
python3 -m assistant_framework.cli skill advancetodos --args '{"amount":"-2d"}'

# Preview todos due November 16 through November 20, inclusive.
python3 -m assistant_framework.cli skill advancetodos --args '{"amount":"1 week","start_date":"2026-11-16","end_date":"2026-11-20","dry_run":true}'

# Apply a 90-minute shift to that date range.
python3 -m assistant_framework.cli skill advancetodos --args '{"amount":"PT90M","start_date":"2026-11-16","end_date":"2026-11-20"}'
```

Taskwarrior's [duration documentation](https://taskwarrior.org/docs/durations/)
explains why assigning `due:2d` would be relative to now. This skill instead
computes each due timestamp explicitly, preserving the spacing between tasks.
