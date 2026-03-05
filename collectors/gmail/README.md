# Gmail collector

Collects recent emails into `workspace/gmail.recent` using Google Agentic CLI.

## Multi-account setup
1. In `agent_config.json`, add `collectors.gmail.accounts`.
2. For each account, set `name` and a per-account `command`.
3. Run `python3 run_collectors.py --workspace workspace --collectors collectors --config agent_config.json`.

Example:
```json
"gmail": {
  "accounts": [
    {"name": "personal", "command": "google-agentic gmail list --account personal --format json --limit 50"},
    {"name": "work", "command": "google-agentic gmail list --account work --format json --limit 50"}
  ]
}
```

## File structure
- `collectors/gmail/__init__.py`
- `collectors/gmail/README.md`
