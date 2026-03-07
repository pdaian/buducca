# Telegram recent collector

Collects recent Telegram messages into `workspace/telegram.recent` and stores state in `workspace/collectors/telegram_recent.offset`.

## Multi-account setup
1. Configure `collectors.telegram_recent.accounts`.
2. For each account, provide either `collector_bot_token` or `user_client.enabled=true` (+ API credentials).
3. If using user mode, run one-time signup:
   `python3 -m collectors.telegram_recent.signup --config agent_config.json`
4. Run collectors:
   `python3 -m assistant_framework.cli collectors --workspace workspace --collectors collectors --config agent_config.json`

## File structure
- `collectors/telegram_recent/__init__.py`
- `collectors/telegram_recent/README.md`

## Token ownership safety (important)
- Do **not** reuse the same Telegram bot token for both the frontend bot (`config.json` → `telegram.bot_token`) and this collector in bot-polling mode (`collector_bot_token` / `bot_token`).
- Telegram allows only one `getUpdates` consumer per token; sharing it causes dropped updates and race conditions.
- Use one of these alternatives:
  - Prefer Telegram user-client mode for collectors (`user_client.enabled=true`).
  - Or use a separate dedicated collector bot token (`collector_bot_token`).

