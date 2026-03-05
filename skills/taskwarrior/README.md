# Taskwarrior skill

## What it does
Runs Taskwarrior workflows from the assistant (`list`, `add`, `modify`, `done`).

## Dependencies
- `task` CLI installed and available on `PATH`.
- Python standard library (`subprocess`).
- `assistant_framework.workspace.Workspace` (internal).

## Usage
```bash
python3 run_skill.py taskwarrior --args '{"action":"list"}'
python3 run_skill.py taskwarrior --args '{"action":"add","description":"Buy milk","project":"Home"}'
```
