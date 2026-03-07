# Learn skill

## What it does
Appends durable, reusable one-line learnings to `workspace/learnings`.

Use it to save things that should help in future prompts (preferences, constraints, recurring facts, workflow notes).

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
