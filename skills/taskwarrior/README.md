# Taskwarrior skill

## What it does
Runs Taskwarrior workflows from the assistant (`list`, `add`, `modify`, `done`).

## Dependencies
- `task` CLI installed and available on `PATH`.
- Python standard library (`subprocess`).
- `assistant_framework.workspace.Workspace` (internal).

## Usage
```bash
python3 -m assistant_framework.cli skill taskwarrior --args '{"action":"list"}'
python3 -m assistant_framework.cli skill taskwarrior --args '{"action":"add","description":"Buy milk","project":"Home"}'
```

## Args schema
```ts
{
  action: "list" | "add" | "modify" | "done";
  id?: number;
  description?: string;
  project?: string;
  priority?: "H" | "M" | "L";
  due?: string;
}
```
