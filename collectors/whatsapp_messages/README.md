# WhatsApp messages collector

Collects recent WhatsApp messages into `workspace/whatsapp.messages.recent` via an external export command.

## Auth flow
Use one-time WhatsApp Web QR login:

```bash
python3 run_whatsapp_collector_signup.py --config agent_config.json
```

The QR output/log is written to `workspace/collectors/whatsapp_qr.txt` by default.

## Debian dependencies
This collector is dependency-light in Python and expects an external command/script (often Node-based) for WhatsApp Web auth and export.
