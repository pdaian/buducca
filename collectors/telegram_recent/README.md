# Telegram recent collector

Collects recent Telegram messages into `workspace/telegram.recent` and stores state in `workspace/collectors/telegram_recent.offset`.

## Multi-account setup
1. Configure `collectors.telegram_recent.accounts`.
2. For each account, provide either `collector_bot_token` or `user_client.enabled=true` (+ API credentials).
3. If using user mode, run one-time signup:
   `python3 -m collectors.telegram_recent.signup --config agent_config.json`
4. Run collectors:
   `python3 run_collectors.py --workspace workspace --collectors collectors --config agent_config.json`

## File structure
- `collectors/telegram_recent/__init__.py`
- `collectors/telegram_recent/README.md`
