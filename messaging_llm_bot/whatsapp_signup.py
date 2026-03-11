from __future__ import annotations

import argparse

WHATSAPP_SETUP_MESSAGE = """WhatsApp signup is not automated by BUDUCCA.

How the backend works:
- BUDUCCA runs `whatsapp.receive_command` to fetch inbound messages as JSON.
- BUDUCCA runs `whatsapp.send_command` to send replies, replacing `{recipient}` and `{message}`.
- A separate WhatsApp bridge owns the actual linked-device session and QR login.

Exact repo commands:
- `cp -R config.example config`
- `python3 -m messaging_llm_bot.whatsapp_signup --config config`

Then edit `config/whatsapp.json` so it points at your real bridge commands instead of the built-in stub:
- `receive_command`: `["python3", "/opt/whatsapp-bridge/bridge.py", "receive", "--session", "data/whatsapp-personal"]`
- `send_command`: `["python3", "/opt/whatsapp-bridge/bridge.py", "send", "--session", "data/whatsapp-personal", "--recipient", "{recipient}", "--message", "{message}"]`

Typical bridge signup flow:
- Start your bridge in QR/pairing mode, for example:
  `python3 /opt/whatsapp-bridge/bridge.py pair --session data/whatsapp-personal`
- Scan the QR code from WhatsApp on your phone:
  WhatsApp -> Settings -> Linked Devices -> Link a Device
- Keep the bridge session/state files persisted so future runs stay linked.
- Test the bridge directly:
  `python3 /opt/whatsapp-bridge/bridge.py receive --session data/whatsapp-personal`
  `python3 /opt/whatsapp-bridge/bridge.py send --session data/whatsapp-personal --recipient "group:Family|g1" --message "test"`
- Start BUDUCCA:
  `python3 run_bot.py --config config`

The built-in `messaging_llm_bot.whatsapp_client` module is only a JSON command adapter. It does not create the QR code or perform WhatsApp account linking by itself.
"""


def run_signup(config_path: str) -> int:
    _ = config_path
    print(WHATSAPP_SETUP_MESSAGE)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WhatsApp signup helper")
    parser.add_argument("--config", default="config", help="Bot config JSON file or directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(run_signup(args.config))


if __name__ == "__main__":
    main()
