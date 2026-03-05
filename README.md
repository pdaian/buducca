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
- ✅ **Todo manager:** run local Taskwarrior commands from chat (`list`, `add`, `modify`, `done`) with project/due support.
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

# File skill examples (bulk list args)
python3 run_skill.py file --args '{"action":"read","paths":["telegram.recent","collector_status.json"]}'
python3 run_skill.py file --args '{"action":"write","paths":["notes/today.txt","notes/tomorrow.txt"],"contents":["Top priorities","Plan ahead"]}'
python3 run_skill.py file --args '{"action":"append","paths":["notes/today.txt","notes/tomorrow.txt"],"content":"\n- ship update"}'
python3 run_skill.py file --args '{"action":"create_dir","directories":["notes/archive","notes/drafts"]}'
python3 run_skill.py file --args '{"action":"move","paths":["notes/today.txt","notes/tomorrow.txt"],"destination_dir":"notes/archive"}'
python3 run_skill.py file --args '{"action":"delete_dir","directories":["notes/drafts"]}'

# Web search (DuckDuckGo, no API key)
python3 run_skill.py web_search --args '{"query":"latest python 3.12 release notes"}'
python3 run_skill.py web_search --args '{"query":"rust tokio tutorial","max_results":5}'

# Taskwarrior examples
python3 run_skill.py taskwarrior --args '{"action":"list"}'
python3 run_skill.py taskwarrior --args '{"action":"add","description":"Buy milk","project":"Home","due":"tomorrow"}'
python3 run_skill.py taskwarrior --args '{"action":"modify","id":"3","project":"Errands","due":"eod"}'
python3 run_skill.py taskwarrior --args '{"action":"done","id":"3"}'

# Reset generated local state
python3 reset_workspace.py --dry-run
python3 reset_workspace.py --yes
```

## Voice notes with OpenAI Whisper CLI

When `runtime.enable_voice_notes` is `true`, `runtime.voice_transcribe_command` can call the Python `whisper` command directly. The bot replaces:

- `{input}` with the downloaded voice-note path
- `{input_dir}` with the temporary directory containing that file

Example command array for `config.json`:

```json
"voice_transcribe_command": [
  "whisper",
  "--model", "base.en",
  "--output_dir", "{input_dir}",
  "--output_format", "txt",
  "{input}"
]
```

## Optional: Telegram user-account collection (QR login)

If you need messages a normal bot token cannot access:

1. `pip install telethon`
2. Set `collectors.telegram_recent_collector.user_client.enabled = true` in `agent_config.json`
3. Add your `api_id` and `api_hash`
4. Re-run collectors and complete one-time QR login

## Debug mode

Set `runtime.debug` to `true` in `config.json` to force DEBUG logging and print full LLM request/response payloads plus request timing data. You can also set `runtime.log_level` to `"DEBUG"` to enable the same model-call verbosity.

## Timeout tuning (including 10x)

If your local model is slow, increase `runtime.request_timeout_seconds` in `config.json`.
For example, to increase from 30 seconds to 300 seconds (10x):

```json
"runtime": {
  "request_timeout_seconds": 300
}
```

This timeout is used by outbound HTTP calls made by the bot (including LLM requests).

## Data locations

- `workspace/telegram.recent` — recent Telegram message snapshots
- `workspace/collector_status.json` — collector health/status
- `workspace/collectors/telegram_recent.offset` — collector checkpoint state

That's it: one local workspace, one small backend, one assistant you control. 🔐
