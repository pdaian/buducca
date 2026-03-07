# Signal messages collector

Collects recent Signal messages into `workspace/signal.messages.recent` using `signal-cli` JSON output.

## Multi-account setup
1. Link each account/device first (repeat signup for each device identity).
2. Configure `collectors.signal_messages.accounts` with `name`, `device_name`, and optional `command`.
3. Run collectors.

Signup command:
```bash
python3 -m collectors.signal_messages.signup --config agent_config.json
```

## Device-identity safety with Signal frontend
If you also configure the Signal frontend bot (`config.signal.account`), **do not** use the same Signal identity/device as a collector `device_name` by default.

Why: both frontend receive and collector receive can consume from the same message stream, causing consumer contention and potentially missing/dropped messages in one side.

Recommended patterns:
- Link a separate Signal device identity for collector ingestion.
- Or disable the `signal_messages` collector account that would collide.
- Only if you intentionally accept this risk, set `runtime.allow_signal_collector_device_collision=true` in `config.json`.

## File structure
- `collectors/signal_messages/__init__.py`
- `collectors/signal_messages/README.md`
