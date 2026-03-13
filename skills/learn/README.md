# Learn skill

## What it does
Appends durable, reusable one-line learnings to `workspace/learnings`.

Use it to save durable facts that should be available in future prompts by default.
The skill also writes a structured fact record under `workspace/assistant/facts/` with `source: "learn"`.
Only those learn-generated fact records are auto-included in prompts by default.
Other stored data such as birthdays, contacts, notes, tasks, and routines is not auto-included.

## Dependencies
- Python standard library only.
- `assistant_framework.workspace.Workspace` (internal).

## Usage
```bash
python3 -m assistant_framework.cli skill learn --args '{"learning":"User prefers concise answers unless asked for detail."}'
```

## Args schema
```ts
{
  learning: string;
  text?: string;
  line?: string;
  message?: string;
}
```
