# Gmail collector

Collects recent emails into `workspace/gmail.recent`.

## Multi-account setup
1. In `agent_config.json`, add `collectors.gmail.accounts`.
2. For each account, set `name` and a per-account `command`.
3. Each command should point to one self-contained script that prints Gmail messages as JSON.
3. Run `python3 -m assistant_framework.cli collectors --workspace workspace --collectors collectors --config agent_config.json`.

Example:
```json
"gmail": {
  "accounts": [
    {"name": "personal", "command": "python3 /absolute/path/to/gmail_personal_export.py"},
    {"name": "work", "command": "python3 /absolute/path/to/gmail_work_export.py"}
  ]
}
```

The exporter script must emit either:
- a JSON list of message objects
- or `{"messages": [...]}`

Generated workspace files:
- `workspace/gmail.recent`
- `workspace/collectors/gmail.state.json`

## File structure
- `collectors/gmail/__init__.py`
- `collectors/gmail/README.md`
