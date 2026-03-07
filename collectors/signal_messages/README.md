# Signal messages collector

Collects recent Signal messages into `workspace/signal.messages.recent` using `signal-cli` JSON output.

## Attachment handling (explicit default)
By default, this collector receives with `--ignore-attachments` enabled (`ignore_attachments: true`). This keeps polling light, but it drops attachment/voice-note payload context from collector snapshots.

You can configure behavior globally or per account:

```json
{
  "collectors": {
    "signal_messages": {
      "ignore_attachments": false,
      "accounts": [
        {
          "name": "primary",
          "device_name": "+15550001111",
          "ignore_attachments": false
        }
      ]
    }
  }
}
```

You can also fully override `command` per account if you need custom `signal-cli receive` flags.

## Shared-account mode caveat
If your bot frontend (`config.json > signal.account`) and this collector use the same Signal account/device, shared-account mode can lose voice/attachment context when `ignore_attachments` is enabled. This mode is unsupported unless you explicitly configure attachment handling to match your needs.

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
