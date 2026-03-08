from __future__ import annotations

import argparse

SIGNAL_SETUP_MESSAGE = """Signal signup is no longer automated by BUDUCCA.

Please set up signal-cli with your preferred method, then return to BUDUCCA:
- Phone number registration
- Linked-device QR flow

Relevant docs:
- signal-cli wiki: https://github.com/AsamK/signal-cli/wiki
- signal-cli registration methods: https://github.com/AsamK/signal-cli/wiki/Registration-with-signal-cli
"""


def run_signup(config_path: str) -> int:
    _ = config_path
    print(SIGNAL_SETUP_MESSAGE)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Signal signup helper")
    parser.add_argument("--config", default="config.json", help="Bot config JSON file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(run_signup(args.config))


if __name__ == "__main__":
    main()
