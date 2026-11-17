# Memory skill

## What it does

Reads and maintains structured records under `assistant/people`,
`assistant/tasks`, `assistant/routines`, and `assistant/facts` in the workspace.
Writes validate and normalize a record, replace its JSON file, and append an
event to that area's `history.jsonl`.

## Operations

| Action | Required fields | Result |
| --- | --- | --- |
| `get` (default) | `area`, `id` | One JSON record or a not-found message |
| `list` | `area` | JSON array of records, possibly empty |
| `upsert` | `area`, `record` | Normalized JSON record |
| `delete` | `area`, `id` | Deletion or not-found message |

`upsert` replaces the whole record; it does not merge fields. Read the existing
record, retain fields that should survive (including extension and bookkeeping
fields), then submit the complete replacement with its ID inside `record.id`.
A top-level `id` does not override a nested record's ID. Without `record.id`,
the ID is derived from `name`, `title`, `subject`, or `statement`; duplicate names
can overwrite the same record. Prefer explicit stable IDs for distinct entities.

## Record fields

| Area | Required inside `record` | Useful optional fields and defaults |
| --- | --- | --- |
| `people` | `name` | `contact`, `notes` default to empty strings |
| `facts` | `statement` | `source` defaults to `memory`; `confidence` to `confirmed` |
| `tasks` | `title` | `status` defaults to `open`, `kind` to `task`; `details`, `due_at`, `remind_at`, `notify_target` |
| `routines` | `title`, `schedule` | `enabled` defaults to true; `instructions`, `notify_target`, `next_run_at` |

Use ISO-8601 timestamps with a UTC offset or `Z` for scheduled fields. A routine
schedule needs `frequency` (`hourly`, `daily`, or `weekly`). `interval` defaults
to 1 and must be positive. Daily/weekly schedules default to hour 9, minute 0;
weekly `weekday` defaults to 0 (Monday). Use hours 0–23, minutes 0–59, weekdays
0–6, and an IANA `timezone` (default `UTC`). If omitted, `next_run_at` is calculated.

`notify_target` contains `backend` and `conversation_id`. The running bot can
dispatch due task/routine notices; absent targets fall back to its hourly target.
Routine `instructions` are included as notice text, not executed as an agent task.
Use [add_reminder](../add_reminder/README.md) for the direct-message reminder
workflow with minute-boundary dispatch and explicit daylight-saving behavior.

## Guidance

- Use this for structured workspace records. [Taskwarrior](../taskwarrior/README.md)
  uses a separate database; changes do not synchronize between them.
- Preserve provenance and uncertainty in facts. The default `confirmed` label
  is not evidence of verification; set confidence to reflect what is known.
- Use [learn](../learn/README.md) for concise context needed in future prompts.
  Other records are generally retrieved as needed, except facts marked
  `source: "learn"`, which the runtime previews automatically.
- Errors can be plain text; do not assume every result is JSON. Listing skips
  malformed JSON files. Writes and history appends are separate operations;
  read back the record after an uncertain failure before retrying.
- Deleting a record appends a history event; it does not erase prior copies
  from `history.jsonl`. Do not store secrets or promise complete erasure.

Uses the Python standard library, workspace files, and IANA timezone data for
routine schedules. Prefer canonical fields; legacy flat upsert arguments are
accepted but are less explicit than a nested `record`.

## Args schema

```ts
{
  action?: "get" | "list" | "upsert" | "delete";
  area: "people" | "tasks" | "routines" | "facts";
  id?: string;
  record?: {
    id?: string;
    name?: string;
    notes?: string;
    contact?: string;
    statement?: string;
    source?: string;
    confidence?: string;
    title?: string;
    status?: string;
    due_at?: string;
    remind_at?: string;
    kind?: string;
    details?: string;
    notify_target?: { backend: string; conversation_id: string | number };
    enabled?: boolean;
    instructions?: string;
    schedule?: {
      frequency: "hourly" | "daily" | "weekly";
      interval?: number;
      hour?: number;
      minute?: number;
      weekday?: number;
      timezone?: string;
    };
    next_run_at?: string;
  };
}
```

## Usage

```bash
python3 -m assistant_framework.cli skill memory --args '{"action":"list","area":"people"}'
python3 -m assistant_framework.cli skill memory --args '{"action":"upsert","area":"people","record":{"id":"alex","name":"Alex","notes":"Prefers email for scheduling"}}'
python3 -m assistant_framework.cli skill memory --args '{"area":"people","id":"alex"}'
```
