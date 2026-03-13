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

- `vLLM` if you want a serious OpenAI-compatible serving layer for larger open models
- `Ollama` if you want the fastest local setup on one machine

Relevant docs:

- <https://docs.vllm.ai/>
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

### Google Fi

Google Fi also uses Playwright:

```bash
pip install playwright
python3 -m playwright install chromium
python3 -m messaging_llm_bot.google_fi_client receive --headful
```

More detail: [`docs/frontends.md`](./frontends.md)

## 4. Run the bot

```bash
python3 run_bot.py --config config
```

Useful runtime files:

- `workspace/logs/agenta_queries.history`: handled interactions and replies
- `workspace/logs/{backend}.history`: outgoing frontend traffic
- `workspace/logs/traces/*.json`: full request traces
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
- `/skill` lists loaded skills
- `/skill <skill_name>` shows skill docs and args
- `/skill <skill_name> {"key":"value"}` runs a skill directly

The full frontend command behavior is documented in [`docs/frontends.md`](./frontends.md).

## 7. Where to go next

- Frontend setup and safety notes: [`docs/frontends.md`](./frontends.md)
- Architecture and extension points: [`docs/developer-guide.md`](./developer-guide.md)
- Top-level overview: [`README.md`](../README.md)
