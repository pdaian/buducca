# Getting started

## 1) Prepare config files

```bash
cp -R config.example config
```

## 2) Run collectors

```bash
python3 -m assistant_framework.cli collectors --workspace workspace --collectors collectors --config config/collectors
```

## 3) Run the messaging bot

```bash
python3 run_bot.py --config config
```

## Useful local commands

```bash
# Run a skill manually
python3 -m assistant_framework.cli skill summarize_workspace --workspace workspace --skills skills --args '{"max_items": 20}'

# Reset generated local state
python3 reset_workspace.py --dry-run
python3 reset_workspace.py --yes
```

## Workspace files you will care about

- `workspace/collector_status.json` — collector health and loop state.
- `workspace/collector_status.json` also includes each loaded collector's declared generated workspace files.
- `workspace/logs/agenta_queries.history` — handled incoming interactions and replies.
- `workspace/logs/{backend}.history` — JSONL frontend traffic logs for outgoing replies and any frontend messages the bot persists by backend.
- `workspace/logs/traces/*.json` — one JSON trace per handled request, including the last message, built prompt, retrieved evidence, intermediate skill steps, and final reply or error.
- `workspace/hourly` — optional plaintext instructions evaluated once per hour at the top of the hour.
- `workspace/hourly_status.json` — last completed hourly slot.
- `workspace/telegram.recent` — unanswered Telegram snapshots when storage is enabled.
- `workspace/telegram.messages.recent` — legacy Telegram snapshots file still read for compatibility, but no longer written.
- `workspace/signal.messages.recent` — unanswered Signal snapshots when storage is enabled.
- `workspace/whatsapp.messages.recent` — unanswered WhatsApp snapshots when storage is enabled.
- `workspace/google_fi.messages.recent` — unanswered Google Fi message snapshots when storage is enabled.
- `workspace/google_fi.calls.recent` — Google Fi call-event snapshots.
