# add_reminder

## What it does

Adds a reminder message with a stable ID and a one-off (`once`), daily, or weekly schedule. Also lists reminders, changes them by ID, and removes them by ID. The bot sends the prepared message through Telegram, Signal, WhatsApp, or Android without requesting a model response at delivery time.

The reminder service runs independently of chat requests and hourly model routines. It checks due reminders at startup and on every minute boundary. Each due reminder has its own delivery worker, so an unrelated blocked send does not hold up the others. The bot process must be running for delivery; actual arrival also depends on the messaging backend.

## Args schema

```ts
{
  operation?: "add" | "list" | "update" | "remove";
  id?: string;
  message?: string;
  at?: string;
  unix_time?: number;
  frequency?: "once" | "daily" | "weekly";
  timezone?: string;
  backend?: "telegram" | "signal" | "whatsapp" | "android";
  conversation_id?: string | number;
}
```

- `operation` defaults to `add`. Add requires `message` and either `at` (ISO-8601 date and time) or `unix_time` (integer epoch seconds).
- Chat calls default to the current backend/conversation and `llm.system_prompt_timezone`. Standalone harness or CLI calls must supply `backend` and `conversation_id`; their timezone defaults to `UTC`.
- `frequency` defaults to `once`. Daily reminders use the first occurrence's local time; weekly reminders also use its weekday. Specify an IANA `timezone` to preserve the local time across daylight-saving changes, even when `at` includes an offset.
- Times with seconds round **up** to the next minute. New schedules must start in the future. An offset-free `at` uses `timezone`. Ambiguous local start times use the earlier occurrence; include a UTC offset to select the later one. Nonexistent local start times are rejected.
- `update` requires `id` and at least one field to change. Omitted fields remain unchanged. A new `at` or `unix_time` resets the recurrence anchor. Changing only `timezone` or `frequency` preserves the next scheduled instant and anchors future repetitions there.
- `remove` takes only `id`; `list` takes no other arguments. List returns the active records and IDs. Completed one-off reminders are no longer active.

## Guidance

- Resolve relative dates against the current conversation time and use the
  intended IANA timezone. Ask only when an unresolved time or destination would
  materially change the reminder. Pass either `at` or `unix_time`, never both.
- Use the returned ID for edits and report the saved time, timezone, and repeat
  frequency. List existing reminders before retrying an uncertain add; repeated
  adds create separate IDs and can produce duplicate messages.
- Save the text that should actually be delivered. Delivery does not research
  current information or execute instructions embedded in the message.
- Use [taskwarrior](../taskwarrior/README.md) for its task database and
  [memory](../memory/README.md) for structured workspace records. These stores
  do not synchronize automatically with this reminder journal.

## Examples

Create a one-off message in the current chat:

```json
{"skill_call":{"name":"add_reminder","args":{"message":"Leave for the appointment","at":"2027-01-05T14:25","frequency":"once"},"done":false}}
```

Create a weekly reminder from a standalone harness:

```json
{"message":"Review the coming week","at":"2027-01-04T09:00","frequency":"weekly","timezone":"America/New_York","backend":"telegram","conversation_id":"123"}
```

Add returns JSON containing `status` and `reminder`, including the generated `id`. Use that ID to manage the reminder:

```json
{"operation":"list"}
{"operation":"update","id":"<returned ID>","message":"Review and plan the coming week","at":"2027-01-04T09:30"}
{"operation":"remove","id":"<returned ID>"}
```

Example timestamps must be replaced with a future occurrence appropriate to the
request. `done: false` lets the model inspect the result before confirming it;
validation errors are plain text rather than a success record.

## Delivery and storage

State persists in `workspace/assistant/reminders.jsonl` as an append-only journal: the latest complete record for an ID wins; deletion records mark removals and completed one-offs. This preserves additions and edits made while a delivery is running. The journal may be inspected as text; use `list` for the active view. As with the bot's other mutable workspace state, use one bot process per workspace and make changes through its skills.

On restart, an overdue reminder delivers once. A recurring reminder then advances to the first future occurrence instead of sending every missed repetition. A recurrence in a daylight-saving gap shifts forward by the gap for that occurrence; later occurrences return to the original local time. A repeated clock hour produces only one occurrence.

Failed sends remain pending and retry no sooner than one minute later. Read-only backends retain their reminders without sending. Updates/removals affect future dispatch; a message already being sent cannot be recalled. A crash after a successful send but before its completion is recorded, or an ambiguous network failure, can cause a duplicate on retry.

Saved `prompt` reminders keep their model-driven execution path. They appear in `list` and can be removed, but their prompts are not converted to direct messages by `update`. Use [schedule_prompt](../schedule_prompt/README.md) to create or edit prompts that execute later with the normal model and skill chains, including existing legacy prompt reminders.

## Dependencies and policy

Uses Python's standard library and the configured messaging frontend. No additional scheduler or model configuration is required. Action policy names are `add_reminder.add`, `add_reminder.update`, and `add_reminder.remove`; listing is read-only. Mutations follow the repository's existing approval policy for workspace writes.
