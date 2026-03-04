# Telegram LLM Bot Framework (Python, minimal dependencies)

A lightweight Telegram bot framework using only Python's standard library.

## What it does

- Logs in using your Telegram bot key.
- Receives text messages with long polling.
- Sends message context to a configurable OpenAI-compatible endpoint.
- Returns model replies to Telegram.

## Features

- **No third-party dependencies** (stdlib only).
- **Config file driven** (`config.json`).
- **OpenAI-compatible endpoint path is configurable** (default: `/chat/completions`).
- **Per-chat short conversation memory** (bounded).
- **Optional chat allowlist** using `allowed_chat_ids`.
- **Auto-splits long replies** to Telegram's message size limit.

## Files

- `run_bot.py` – entrypoint.
- `telegram_llm_bot/config.py` – config dataclasses + validation.
- `telegram_llm_bot/http.py` – minimal JSON HTTP client.
- `telegram_llm_bot/telegram_client.py` – Telegram API wrapper.
- `telegram_llm_bot/llm_client.py` – OpenAI-compatible API wrapper.
- `telegram_llm_bot/bot.py` – bot orchestration and memory.

## Quick start

```bash
cp config.example.json config.json
python3 run_bot.py --config config.json
```

## Configuration

```json
{
  "telegram": {
    "bot_token": "123456:token",
    "poll_interval_seconds": 0.2,
    "long_poll_timeout_seconds": 30,
    "allowed_chat_ids": []
  },
  "llm": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "your-key",
    "model": "gpt-4o-mini",
    "endpoint_path": "/chat/completions",
    "system_prompt": "You are a concise and helpful assistant.",
    "temperature": 0.2,
    "max_tokens": 400,
    "history_messages": 8
  },
  "runtime": {
    "request_timeout_seconds": 30,
    "log_level": "INFO"
  }
}
```

### Notes

- `history_messages` is the number of past user+assistant turns retained per chat.
- Validation errors are raised at startup if required config fields are missing.
- Only text messages are processed.
