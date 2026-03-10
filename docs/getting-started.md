# Getting started

## 1) Prepare config files

```bash
cp config.example.json config.json
cp agent_config.example.json agent_config.json
```

## 2) Run collectors

```bash
python3 -m assistant_framework.cli collectors --workspace workspace --collectors collectors --config agent_config.json
```

## 3) Run compressors

```bash
cp compressors/config.example.json compressors/config.json
python3 -m assistant_framework.cli compressors --workspace workspace --compressors compressors --config compressors/config.json
```

## 4) Run the messaging bot

```bash
python3 run_bot.py --config config.json
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
- `workspace/logs/agenta_queries.history` — answered interactions.
- `workspace/compressor_status.json` — compressor health and loop state.
- `workspace/hourly` — optional plaintext instructions evaluated once per hour at the top of the hour.
- `workspace/hourly_status.json` — last completed hourly slot.
- `workspace/telegram.recent` — Telegram snapshots when storage is enabled.
- `workspace/signal.messages.recent` — Signal snapshots when storage is enabled.
- `workspace/whatsapp.messages.recent` — WhatsApp snapshots when storage is enabled.
