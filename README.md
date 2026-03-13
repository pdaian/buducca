<p align="center">
  <img src="docs/assets/buducca-logo.svg" alt="BUDUCCA logo" width="900"/>
</p>

# BUDUCCA — Private, local-first personal assistant 🤖

Run your own Telegram, Signal, WhatsApp, and/or Google Fi assistant with a small Python codebase you can actually read in an afternoon, while keeping local-model reasoning under your control.

[![Join the BUDUCCA Telegram](https://img.shields.io/badge/Join%20our%20Telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/buducca)

> [!IMPORTANT]
> BUDUCCA is pre-release software. If you want to help shape it, join the Telegram group and tell us what you need.

## What BUDUCCA can do

- Run one assistant backend behind multiple messaging frontends (Telegram, Signal, WhatsApp, Google Fi).
- Ingest local data via collectors (Gmail, Slack, Twitter recent activity, Google Calendar).
- Execute local skills for filesystem tasks, task management, web search, workspace learning, and summaries.
- Keep everything in a plain workspace directory so state is inspectable and scriptable.

## Why it feels different

- **Local-first by default:** data, automation, and local-model reasoning stay on your machine for stronger data sovereignty.
- **Simple plugin model:** skills and collectors are regular Python modules, loaded dynamically from disk.
- **Readable architecture:** no giant framework, no hidden telemetry pipeline, no required cloud backend.
- **Hackable workflows:** every meaningful behavior is configurable with JSON + Python, including how local models reason over your data.

## Quick start

> [!WARNING]
> Messaging frontends rely on third-party networks. Transport metadata and platform visibility still apply.

```bash
cp -R config.example config
python3 -m assistant_framework.cli collectors --workspace workspace --collectors collectors --config config/collectors
python3 run_bot.py --config config
```

## Explore and modify

- Start here: [`docs/getting-started.md`](docs/getting-started.md)
- Frontend behavior and safety notes: [`docs/frontends.md`](docs/frontends.md)
- Architecture and extension guide: [`docs/developer-guide.md`](docs/developer-guide.md)

Plugin docs live next to each plugin:

Google Fi CLI: `python3 -m messaging_llm_bot.google_fi_client --help`

- `skills/<name>/README.md`
- `collectors/<name>/README.md`
