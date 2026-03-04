<p align="center">
  <img src="assets/buducca-logo.svg" alt="BUDUCCA logo" width="900"/>
</p>

# BUDUCCA — Private, local-first personal assistant 🤖

Run your own Telegram assistant with a tiny, understandable Python stack.

## Why BUDUCCA

- 🔒 **Privacy-first:** your data stays on your machine.
- 🖥️ **Local-first execution:** collectors, skills, and optional voice transcription run locally.
- 📡 **No telemetry:** no tracking pipeline, no analytics SDK, no hidden reporting.
- 🧩 **Simple backend:** small modular scripts + JSON config, easy to inspect and change.
- 🐣 **Useful with tiny local models:** built for practical automation, not giant cloud-only setups.

## High-value use cases

- 💬 **Telegram personal assistant:** ask for summaries, reminders, context from recent messages.
- ✅ **Todo manager:** run local Taskwarrior commands from chat (`list`, `add`, `done`).
- 🧠 **Workspace summarizer:** turn collected chat history into fast local briefings.
- 🎤 **Private voice-note assistant:** transcribe voice notes using your own local speech CLI.

## Quick start

```bash
cp config.example.json config.json
cp agent_config.example.json agent_config.json
python3 run_collectors.py --workspace workspace --collectors collectors --config agent_config.json
python3 run_bot.py --config config.json
```

## Core commands

```bash
# Run a skill manually
python3 run_skill.py summarize_workspace --workspace workspace --skills skills --args '{"max_items": 20}'

# Taskwarrior examples
python3 run_skill.py taskwarrior --args '{"action":"list"}'
python3 run_skill.py taskwarrior --args '{"action":"add","description":"Buy milk"}'
python3 run_skill.py taskwarrior --args '{"action":"done","id":"3"}'

# Reset generated local state
python3 reset_workspace.py --dry-run
python3 reset_workspace.py --yes
```

## Optional: Telegram user-account collection (QR login)

If you need messages a normal bot token cannot access:

1. `pip install telethon`
2. Set `collectors.telegram_recent_collector.user_client.enabled = true` in `agent_config.json`
3. Add your `api_id` and `api_hash`
4. Re-run collectors and complete one-time QR login

## Data locations

- `workspace/telegram.recent` — recent Telegram message snapshots
- `workspace/collector_status.json` — collector health/status
- `workspace/collectors/telegram_recent.offset` — collector checkpoint state

That's it: one local workspace, one small backend, one assistant you control. 🔐
