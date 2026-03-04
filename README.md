# Personal Assistant Agent Framework (Python, file-first)

This repository includes a lightweight agent framework that is easy to extend for personal assistant workflows.

## Architecture

The framework is built around three pluggable components:

1. **Workspace (`workspace/`)**
   - A local folder where the agent reads/writes durable files.
   - Skills and collectors operate through the same workspace abstraction.

2. **Skills (`skills/*.py`)**
   - Python files that expose a callable `run(workspace, args)`.
   - Loaded dynamically at runtime.

3. **Data Collectors (`collectors/*.py`)**
   - Python files that expose either:
     - `create_collector(config)` returning `{name, interval_seconds, run}`
     - or module globals + `run(workspace)`.
   - Executed by a **separate background runner** (`run_collectors.py`) on intervals.
   - Persist execution stats to `workspace/collector_status.json`.

## Included Collector: Telegram Recent Messages

`collectors/telegram_recent_collector.py` pulls recent Telegram updates and writes newline-delimited JSON records to:

- `workspace/telegram.recent`

It uses a lightweight Telegram Bot API client with token authentication (`TelegramLiteClient`) and stores polling offset in:

- `workspace/collectors/telegram_recent.offset`

## Telegram `/status` command

When running `run_bot.py`, sending `/status` to the bot returns:

- bot uptime and handled message count
- active in-memory chat count
- collector loop metadata (loop count, update time)
- per-collector success/failure counts and `last_success_at`

`/status` reads from `runtime.workspace_dir/runtime.collector_status_file` (defaults to `workspace/collector_status.json`) which is produced by `run_collectors.py`.

## Quick Start

### 1) Prepare collector config

```bash
cp agent_config.example.json agent_config.json
```

Edit `agent_config.json` and set your Telegram bot token for collectors.

### 2) (Optional) Prepare bot config for `/status`

```bash
cp config.example.json config.json
```

By default, bot status reads from `workspace/collector_status.json`.

### 3) Run collectors in background process

```bash
python3 run_collectors.py --workspace workspace --collectors collectors --config agent_config.json
```

### 4) Run Telegram bot

```bash
python3 run_bot.py --config config.json
```

### 5) Run a skill on demand

```bash
python3 run_skill.py summarize_workspace --workspace workspace --skills skills --args '{"max_items": 20}'
```
