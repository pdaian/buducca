from __future__ import annotations

import argparse

WHATSAPP_SETUP_MESSAGE = """WhatsApp signup is not automated by BUDUCCA.

Set up a WhatsApp bridge that exposes receive/send commands, then pair it with WhatsApp Web:
- Start your bridge in QR/pairing mode.
- Scan the QR code from WhatsApp on your phone:
  WhatsApp -> Settings -> Linked Devices -> Link a Device
- Keep the bridge session/state files persisted so future runs stay linked.
- Point `whatsapp.receive_command` and `whatsapp.send_command` in `config/whatsapp.json` at that bridge.

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
