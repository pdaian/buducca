# Slack collector

Collects recent Slack messages into `workspace/slack.recent` from configured export commands.

## Multi-account setup
1. Configure `collectors.slack.accounts` with one entry per workspace/account.
2. Each account requires `name` and `command`.
3. Start collectors with `run_collectors.py`.

## File structure
- `collectors/slack/__init__.py`
- `collectors/slack/README.md`
