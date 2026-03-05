# WhatsApp messages collector

Collects recent WhatsApp messages into `workspace/whatsapp.messages.recent` via external export commands.

## Multi-account setup
1. Complete QR signup for each account/session.
2. Configure `collectors.whatsapp_messages.accounts` with `name`, `session_file`, and `command`.
3. Run collectors.

Signup command:
```bash
python3 -m collectors.whatsapp_messages.signup --config agent_config.json
```

## File structure
- `collectors/whatsapp_messages/__init__.py`
- `collectors/whatsapp_messages/README.md`
