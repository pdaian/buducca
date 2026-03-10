# Slack collector

Collects recent Slack messages into `workspace/slack.recent` from configured export commands.

## Multi-account setup
1. Configure `collectors.slack.accounts` with one entry per workspace/account.
2. Each account requires `name` and `command`.
3. Each command should point to one self-contained script that prints Slack messages as JSON.
3. Start collectors with `python3 -m assistant_framework.cli collectors`.

Generated workspace files:
- `workspace/slack.recent`
- `workspace/collectors/slack.state.json`

## File structure
- `collectors/slack/__init__.py`
- `collectors/slack/README.md`
