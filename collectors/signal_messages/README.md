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

## File structure
- `collectors/signal_messages/__init__.py`
- `collectors/signal_messages/README.md`
