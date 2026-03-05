# Telegram recent connector

## What it does
Collects recent Telegram messages into `workspace/telegram.recent` and stores state in `workspace/collectors/telegram_recent.offset`.

## Dependencies
- Either:
  - Telegram bot token (`collector_bot_token` / `TELEGRAM_COLLECTOR_BOT_TOKEN`), or
  - Telethon user account mode (`pip install telethon`) with `user_client.enabled = true`.
- Python runtime used by the project.

## Usage
Configure in `agent_config.json` under `collectors.telegram_recent` (or legacy `collectors.telegram_recent_collector`) and run:

```bash
python3 run_collectors.py --workspace workspace --collectors collectors --config agent_config.json
```
