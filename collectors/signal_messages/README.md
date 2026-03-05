# Signal messages collector

Collects recent Signal messages into `workspace/signal.messages.recent` using `signal-cli` JSON output.

## Auth flow
Use second-device QR linking once:

```bash
python3 run_signal_collector_signup.py --config agent_config.json
```

This writes QR/link output to `workspace/collectors/signal_qr.txt` by default.

## Debian dependencies
- `signal-cli`
- Java runtime (`default-jre-headless`)

No extra Python packages are required.
