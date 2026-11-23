# schedule_prompt

## What it does

Saves a prompt to execute in the future, then sends the result to the selected conversation. The scheduled turn uses the normal model, current workspace context, conversation history, available skills, and multi-step skill chains. Use this for work to perform later, such as researching a topic, reading updated files, or running a sequence of skills. Use [add_reminder](../add_reminder/README.md) when only a prepared message should be delivered.

Supports one-off (`once`), daily, and weekly schedules, plus listing, updating, and removing saved prompts by stable ID. The bot must be running. Prompts run through the existing scheduler and wait for the normal conversation lock and available model request capacity; long-running work can delay later scheduled prompts.

## Args schema

```ts
{
  operation?: "add" | "list" | "update" | "remove";
  id?: string;
  prompt?: string;
  files?: string | string[];
  at?: string;
  unix_time?: number;
  frequency?: "once" | "daily" | "weekly";
  timezone?: string;
  backend?: "telegram" | "signal" | "whatsapp" | "android";
  conversation_id?: string | number;
}
```

- `operation` defaults to `add`. Add requires a non-empty `prompt` and either `at` (ISO-8601 date and time) or `unix_time` (integer epoch seconds).
- Chat adds default to the current backend/conversation and `llm.system_prompt_timezone`. Standalone CLI calls must supply `backend` and `conversation_id`; their timezone defaults to `UTC`. An explicit different backend also requires its destination.
- `frequency` defaults to `once`. Daily schedules preserve the first occurrence's local clock time; weekly schedules also preserve its weekday. Specify an IANA `timezone` to preserve local time across daylight-saving changes, even if `at` includes an offset.
- Seconds round **up** to the next minute. New schedules must start in the future. An offset-free `at` uses `timezone`. Ambiguous start times use the earlier occurrence; include an offset to select the later one. Nonexistent local start times are rejected.
- `files` supplies optional workspace paths to include at execution time. They must stay inside the workspace, but need not exist when scheduled. Duplicate paths are removed. Missing, unreadable, or non-UTF-8 files are reported in the prompt; large text is excerpted using the existing reminder context limits. The model can use skills to read more. Pass `[]` to clear the file list on update.
- `update` requires `id` and at least one changed field. Omitted fields remain unchanged. A new `at` or `unix_time` resets the recurrence anchor; changing only `timezone` or `frequency` anchors later repetitions at the next scheduled instant.
- `list` takes only `operation` and returns active prompts, including legacy saved-prompt reminders. `remove` takes only `operation` and `id`. This skill cannot edit or remove direct message reminders. Completed one-offs are no longer active.

## Guidance

- Write a self-contained prompt describing the work and desired result. Resolve references such as “that file” while scheduling; conversation history may change before execution. Resolve scheduling dates against the current conversation time, and express dates inside the saved prompt according to when it will run.
- Scheduling stores the instructions; it does not run them immediately. At execution time the model can select and chain any currently enabled skills, subject to the normal action policy and execution limits. Leading model directives such as `/nothink` and `/1` work as in chat.
- Report the returned ID, saved time, timezone, and frequency. List before retrying an uncertain add, because repeated adds create separate schedules.

## Examples

Schedule work in the current chat:

```json
{"skill_call":{"name":"schedule_prompt","args":{"prompt":"Read project-notes.txt, summarize the open decisions, and recommend the next three actions.","files":["project-notes.txt"],"at":"2027-01-05T09:00"},"done":false}}
```

Schedule a weekly workflow from a standalone harness:

```json
{"prompt":"Review my open tasks with taskwarrior, inspect relevant workspace notes, and send a prioritized plan for this week.","at":"2027-01-04T09:00","frequency":"weekly","timezone":"America/New_York","backend":"telegram","conversation_id":"123"}
```

Add/update returns JSON with `status` and `scheduled_prompt`, including its `id`. Manage it with:

```json
{"operation":"list"}
{"operation":"update","id":"<returned ID>","prompt":"Review my open tasks and suggest the single most useful next action.","at":"2027-01-04T09:30"}
{"operation":"remove","id":"<returned ID>"}
```

Replace example dates with future occurrences appropriate to the request. `done: false` lets the model inspect the scheduling result before confirming it. Validation errors are plain text, not success records. Direct CLI skill calls save schedules without the bot's action-policy checks; a running bot using the same workspace performs the later execution.

## Execution and storage

Prompts share the append-only `workspace/assistant/reminders.jsonl` journal with reminder messages, using `delivery: "prompt"`. Existing legacy prompt records remain executable and can be managed here. Updates preserve IDs; completion compares the dispatched record with current state so concurrent edits, removals, or additions are retained. Run one bot process per workspace.

An overdue prompt runs once after restart. A recurring prompt advances to the first future occurrence after successful execution and delivery, skipping missed repetitions. Recurrences in daylight-saving gaps shift forward for that occurrence, then return to the original local time. Repeated clock hours produce one occurrence.

Unavailable or read-only destinations keep prompts pending without starting a model turn. Execution or delivery failures remain pending and retry no sooner than one minute later; editing the schedule clears that delay. Failed attempts and completed skill results are recorded in the normal execution traces. Successful results also enter conversation history and `logs/reminders.history`.

Execution is at least once. A retry after a partially completed skill chain, failed delivery, or crash can repeat actions, including actions completed before the failure. For workflows with external side effects, include duplicate checks in the saved instructions and inspect traces before manually retrying. Updating or removing a prompt cannot undo work already in progress.

## Dependencies and policy

Uses Python's standard library, the configured model, and a configured messaging frontend. No additional scheduler or model configuration is required. Scheduling mutations use `schedule_prompt.add`, `schedule_prompt.update`, and `schedule_prompt.remove` through the existing action policy and audit path; listing is read-only. Each skill invoked when the prompt runs is checked against the policy in effect at execution time.
