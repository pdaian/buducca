# Getting started

This guide is the shortest complete path from fresh checkout to a working BUDUCCA bot.

## 1. Copy the config tree

```bash
cp -R config.example config
```

BUDUCCA loads either one JSON file or a config directory. The example tree is the intended starting point.

See also: [`config.example/README.md`](../config.example/README.md)

## 2. Point BUDUCCA at a modern open model

Edit `config/llm.json`.

The runtime expects an OpenAI-compatible chat-completions endpoint:

- `base_url`: your server root
- `api_key`: bearer token, or a placeholder if your local server ignores auth
- `model`: the model ID exposed by that server
- `endpoint_path`: usually `/chat/completions`

Two practical deployment paths:

- `LM Studio` if you want the simplest OpenAI-compatible local model setup
- `Ollama` if you want the fastest local setup on one machine

Relevant docs:

- <https://lmstudio.ai/>
- <https://docs.ollama.com/openai>

Minimal example for a local OpenAI-compatible server:

```json
{
  "base_url": "http://127.0.0.1:8000/v1",
  "api_key": "local-token",
  "model": "your-open-model",
  "endpoint_path": "/chat/completions",
  "temperature": 0.2,
  "max_tokens": 400
}
```

## 3. Enable one frontend

Pick one path first. Telegram bot mode is the leanest bootstrap.

### Telegram bot mode

Edit `config/telegram.json`:

- set `bot_token`
- optionally set `allowed_chat_ids`
- keep `mode` as `"bot"`

No extra Python package is required for bot-token mode.

### Telegram user mode

If you want full-account Telegram access instead of a bot:

- set `mode` to `"user"`
- set `api_id` and `api_hash`
- install `telethon`
- if you need to force a message resync later, delete the `*.updates.json` file next to `telegram.session_path` and keep the session file unless you also want to log in again

```bash
pip install telethon
```

### Signal

Configure `config/signal.json` around your `signal-cli` commands.

Setup help:

```bash
python3 -m messaging_llm_bot.signal_signup --config config
```

### WhatsApp

The in-repo bridge uses Playwright.

```bash
pip install playwright
python3 -m playwright install chromium
python3 -m messaging_llm_bot.whatsapp_signup --config config
```

More detail: [`docs/frontends.md`](./frontends.md)

### Android via Termux

On the Android device:

```bash
pkg update
pkg install python termux-api
mkdir -p data
: > data/android-events.jsonl
```

Create `config/android.json` from the example and set the exact SMS numbers that may trigger replies:

```json
{
  "account": "android",
  "poll_interval_seconds": 1.0,
  "allowed_sender_ids": [
    "+15551234567"
  ],
  "receive_command": [
    "python3",
    "-m",
    "messaging_llm_bot.android_client",
    "receive",
    "--inbox",
    "data/android-events.jsonl",
    "--state-file",
    "data/android-bridge-state.json"
  ],
  "send_command": [
    "python3",
    "-m",
    "messaging_llm_bot.android_client",
    "send",
    "--recipient",
    "{recipient}",
    "--message",
    "{message}"
  ],
  "read_only": false,
  "store_unanswered_messages": true
}
```

Copy `run_client.py` to the phone and start the built-in Termux client:

```bash
python3 run_client.py run \
  --inbox data/android-events.jsonl \
  --notification-state-file data/termux-notifications-state.json \
  --outbox data/android-sms-outbox.jsonl \
  --outbox-state-file data/android-sms-outbox-state.json \
  --remote-host "$SERVER" \
  --remote-dir "$REMOTE_DIR" \
  --ssh-key "$HOME/.ssh/buducca_android_sync" \
  --include-package org.thoughtcrime.securesms \
  --include-package com.whatsapp \
  --interval-seconds 2
```

This removes the dependency on third-party Android automation apps. The client polls `termux-notification-list`, appends newly seen notifications into `data/android-events.jsonl`, syncs that file to the server, pulls the SMS outbox, and sends queued SMS locally.

If you also want to inject SMS events into the Android bridge, append JSONL rows shaped like:

```json
{"type":"sms","sender_id":"+15551234567","body":"Ping","timestamp":"2026-03-18T09:00:00-04:00"}
```

Grant SMS permission to `Termux:API` before testing send. On the phone, open Android Settings, find Apps, open `Termux:API`, open Permissions, and set SMS to Allow. Android labels vary slightly by version, but the permission must be granted to `Termux:API`, not only to `Termux`.

Verify the receive bridge by running one collection pass and reading it:

```bash
python3 run_client.py collect-notifications once --inbox data/android-events.jsonl --state-file data/termux-notifications-state.json
python3 -m messaging_llm_bot.android_client receive --inbox data/android-events.jsonl --state-file data/android-bridge-state.json
```

The second command should print JSON with any newly collected messages under `"messages"`. Then verify the send bridge:

```bash
python3 -m messaging_llm_bot.android_client send --recipient +15551234567 --message "bridge test"
```

If send fails, fix the `Termux:API` SMS permission before starting the bot.

More detail: [`docs/frontends.md`](./frontends.md)

More detail: [`docs/frontends.md`](./frontends.md)

## 4. Run the bot

```bash
python3 run_bot.py --config config
```

Useful runtime files:

- `workspace/logs/agenta_queries.history`: handled interactions and replies
- `workspace/logs/{backend}.history`: outgoing frontend traffic
- `data/traces/*.json`: full request traces
- `workspace/collector_status.json`: collector state and health

## 5. Add collectors when you need external context

Collectors are optional. Configure only the ones you want under `config/collectors/`.

Run them with:

```bash
python3 -m assistant_framework.cli collectors --workspace workspace --collectors collectors --config config/collectors
```

Collector docs:

- Gmail: [`collectors/gmail/README.md`](../collectors/gmail/README.md)
- Google Calendar: [`collectors/google_calendar/README.md`](../collectors/google_calendar/README.md)
- Reddit top: [`collectors/reddit_top/README.md`](../collectors/reddit_top/README.md)
- Slack: [`collectors/slack/README.md`](../collectors/slack/README.md)
- Twitter recent: [`collectors/twitter_recent/README.md`](../collectors/twitter_recent/README.md)

Optional collector dependency:

```bash
pip install gcsa
```

That package is only needed for the built-in Google Calendar collector.

## 6. Operator commands

### Local CLI

```bash
# Run one skill directly
python3 -m assistant_framework.cli skill summarize_workspace --workspace workspace --skills skills --args '{"max_items": 20}'

# Show the latest prompt sent to the model
python3 -m assistant_framework.cli trace last-prompt --workspace workspace

# Replay the latest trace in plain text
python3 -m assistant_framework.cli trace replay --workspace workspace

# Reset generated local state
python3 reset_workspace.py --dry-run
python3 reset_workspace.py --yes
```

### In-chat commands

- `/status` shows bot uptime and collector status
- `/now` shows one consolidated table with `platform`, `sender`, and `text` from the latest 10 entries in each frontend `.recent` file
- `/skill` lists loaded skills
- `/skill <skill_name>` shows skill docs and args
- `/skill <skill_name> {"key":"value"}` runs a skill directly

The full frontend command behavior is documented in [`docs/frontends.md`](./frontends.md).

## 7. Where to go next

- Frontend setup and safety notes: [`docs/frontends.md`](./frontends.md)
- Architecture and extension points: [`docs/developer-guide.md`](./developer-guide.md)
- Top-level overview: [`README.md`](../README.md)
