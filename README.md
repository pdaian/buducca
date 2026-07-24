<p align="center">
  <img src="docs/assets/buducca-logo.svg" alt="BUDUCCA logo" width="900"/>
</p>

# BUDUCCA

Control plane for messaging-first assistants.

BUDUCCA runs one assistant core across Telegram, Signal, WhatsApp, and Android command bridges, with local workspace state, pluggable collectors, and OpenAI-compatible model endpoints. It is built for people who want modern open models in chat without a heavy SaaS stack.

[![Join the BUDUCCA Telegram](https://img.shields.io/badge/Join%20our%20Telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/buducca)

> [!IMPORTANT]
> BUDUCCA is pre-release software.
>
> **BUDUCCA is not associated with any tokens, token sales, cryptocurrencies, investment vehicles, or other financial products.**

## Why it is different

- Messaging-native: the primary UX is chat, not a web dashboard pretending to be an agent shell.
- Open-model ready: point `llm.json` at any OpenAI-compatible endpoint, including local servers.
- Grounded by files: skills and collectors write plain workspace files you can inspect, script, diff, and back up.
- Small enough to change: most of the repo is straightforward Python, not framework fog.

## Quick start

Copy the example config, wire one model endpoint, enable one frontend, then run the bot:

```bash
cp -R config.example config
$EDITOR config/llm.json
$EDITOR config/telegram.json
python3 run_bot.py --config config
```

For a fuller boot sequence, collector setup, and command reference, see [docs/getting-started.md](docs/getting-started.md).

## Model paths

The model client speaks the OpenAI chat-completions shape. The simplest production path is:

- local `LM Studio` for the simplest OpenAI-compatible desktop setup
- local `Ollama` for fast single-node setup
- local `Qwen3.5-9B` as the current efficient general/tool-use starting point
- local `gpt-oss-20b` when the machine can carry a stronger reasoning profile
- hosted `GPT-5.6 Luna` for efficient volume, `Terra` for balance, or `Sol` for the hardest work
- hosted `DeepSeek V4 Flash` or `V4 Pro` as OpenAI-compatible alternatives

Relevant docs:

- `LM Studio`: <https://lmstudio.ai/>
- `Ollama` OpenAI compatibility: <https://docs.ollama.com/openai>
- Current model profiles and adoption plan: [docs/agent-modernization.md](docs/agent-modernization.md)

## Commands you will actually use

```bash
# Run the bot
python3 run_bot.py --config config

# Run collectors
python3 -m assistant_framework.cli collectors --workspace workspace --collectors collectors --config config/collectors

# Run a skill directly
python3 -m assistant_framework.cli skill summarize_workspace --workspace workspace --skills skills --args '{"max_items": 20}'

# Inspect the runtime skill surface the model sees
python3 -m assistant_framework.cli skills list --skills skills
python3 -m assistant_framework.cli skills inspect summarize_workspace --skills skills

# Inspect the latest trace
python3 -m assistant_framework.cli trace last-prompt --workspace workspace
python3 -m assistant_framework.cli trace replay --workspace workspace
```

In chat, the built-in operator commands are `/status` and `/skill`. Frontend details live in [docs/frontends.md](docs/frontends.md).

Remote Android note:

- The Android/Termux frontend supports a server-side inbox plus SMS outbox flow now. A Termux device can generate its own SSH key, push notification/SMS JSONL files to a limited server account, pull queued SMS commands back down, and send them locally via `Termux:API`. Setup details, file permissions, and the exact commands are in [docs/frontends.md](docs/frontends.md).
- The Android helper now lives at [`scripts/run_client.py`](scripts/run_client.py). Running it with no arguments prints the setup guide and default local file layout.
- The repo also includes [`scripts/update_frontends.py`](scripts/update_frontends.py) for updating detected Signal, Telegram, WhatsApp, and similar frontend packages on supported package managers.

## Documentation map

- Start here: [docs/getting-started.md](docs/getting-started.md)
- AI stack overview and comparisons: [docs/ai-stack.md](docs/ai-stack.md)
- Agent modernization gameplan and current backend profiles: [docs/agent-modernization.md](docs/agent-modernization.md)
- Frontends and slash commands: [docs/frontends.md](docs/frontends.md)
- Architecture and extension points: [docs/developer-guide.md](docs/developer-guide.md)
- Plugin docs: `skills/<name>/README.md` and `collectors/<name>/README.md`
