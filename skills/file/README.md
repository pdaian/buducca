# File skill

## What it does
Provides workspace-safe file and directory operations in bulk:
- `read`
- `write`
- `append`
- `move`
- `create_dir`
- `delete_dir`

## Dependencies
- Python standard library only.
- `assistant_framework.workspace.Workspace` (internal).

## Usage
```bash
python3 run_skill.py file --args '{"action":"read","paths":["telegram.recent"]}'
python3 run_skill.py file --args '{"action":"write","paths":["notes/today.txt"],"content":"hello"}'
```

Use `paths` for file actions and `directories` for directory actions.
