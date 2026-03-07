#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from collectors.signal_messages import signup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Signal setup guidance for collector")
    parser.add_argument("--config", default="agent_config.json", help="Agent config JSON file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        config = {}
    collector_cfg = config.get("collectors", {}).get("signal_messages", {})
    code = signup(collector_cfg)
    if code != 0:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
