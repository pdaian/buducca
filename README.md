<p align="center">
  <img src="assets/buducca-logo.svg" alt="Rainbow cartoony stuffed turtle in a lush forest with a BUDUCCA rainbow banner" width="900"/>
</p>

# Personal Assistant Agent Framework (Python, file-first)

This repo gives you a simple way to run your own personal assistant with local files.

## What this project does

You can use this project to:
- run a Telegram bot
- collect recent Telegram messages into local files
- run small assistant "skills" against your workspace data

## What makes it unique

- **File-first design:** everything important is saved in your local workspace folder.
- **Modular pieces:** you can add collectors and skills without changing the whole app.
- **Works with both Telegram bot + user account collection:** the Telegram collector can use a normal bot token, and can also optionally use a user session (QR login) to read messages bots cannot access.

---

## Common things you may want to do

## 1) Start from example config files

```bash
cp agent_config.example.json agent_config.json
cp config.example.json config.json
```

- `agent_config.json` is for collectors (including a collector-specific Telegram token).
- `config.json` is for the main Telegram bot runtime token and LLM settings.

## 2) Collect recent Telegram messages (bot token flow)

1. Open `agent_config.json`.
2. Set `collectors.telegram_recent_collector.collector_bot_token` (this can be different from the main bot token in `config.json`).
3. Start collectors:

```bash
python3 run_collectors.py --workspace workspace --collectors collectors --config agent_config.json
```

Collected messages are written to `workspace/telegram.recent`.

## 3) Collect Telegram messages from your **user account** (QR flow)

Use this if you want messages a bot account usually cannot see.

1. Install Telethon once:

```bash
pip install telethon
```

2. In `agent_config.json`, enable:
   - `collectors.telegram_recent_collector.user_client.enabled = true`
   - set your `api_id` and `api_hash`
3. Run collectors (same command as above).
4. On first run, the app prints a login URL for QR sign-in. Open it and complete Telegram device linking.
5. Future runs reuse the saved session automatically.


## 4) Enable local voice note parsing in the **main bot**

This keeps transcription on your own machine (no external speech API required).

1. Install a local speech-to-text CLI (for example `whisper.cpp`'s `whisper-cli`).
2. In `config.json`, set:
   - `runtime.enable_voice_notes = true`
   - `runtime.voice_transcribe_command` to a command list that prints transcript text to stdout.
3. Include `{input}` in the command where the downloaded Telegram voice-note file path should be inserted.

Example:

```json
"runtime": {
  "enable_voice_notes": true,
  "voice_transcribe_command": [
    "whisper-cli",
    "--model",
    "models/ggml-base.en.bin",
    "--file",
    "{input}"
  ]
}
```

When a voice note arrives, the bot downloads the file from Telegram, runs your local command, and feeds the transcript to the LLM as normal chat text.

---


## 5) Run the Telegram bot

```bash
python3 run_bot.py --config config.json
```

You can send `/status` to the bot to check collector health and runtime info.

## 6) Run a skill on demand

```bash
python3 run_skill.py summarize_workspace --workspace workspace --skills skills --args '{"max_items": 20}'
```

---


## 7) Use the Taskwarrior skill

If you have a local `task` CLI installed, you can manage todos directly:

```bash
# List tasks
python3 run_skill.py taskwarrior --args '{"action":"list"}'

# Add a task
python3 run_skill.py taskwarrior --args '{"action":"add","description":"Buy milk"}'

# Mark task 3 as done
python3 run_skill.py taskwarrior --args '{"action":"done","id":"3"}'
```


### Telegram skill invocation behavior

The bot now injects a dynamic skill guide into the system prompt at runtime using files in `runtime.skills_dir` (default `skills`).

- It lists every available skill name + description for the model.
- If a user explicitly asks to run a skill, the model should answer with strict JSON:

```json
{"skill_call": {"name": "taskwarrior", "args": {"action": "list"}}}
```

When this output is detected, the bot executes the skill locally and sends the skill output back to Telegram.

## Where outputs go

- Recent Telegram collection output: `workspace/telegram.recent`
- Collector status file: `workspace/collector_status.json`
- Telegram collector state: `workspace/collectors/telegram_recent.offset`
