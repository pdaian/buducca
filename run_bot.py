#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging

from telegram_llm_bot import BotRunner, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Telegram + OpenAI-compatible bot")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the bot configuration file (JSON)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    configured_level = getattr(logging, config.runtime.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=logging.DEBUG if config.runtime.debug else configured_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    runner = BotRunner(config)
    runner.run_forever()


if __name__ == "__main__":
    main()
