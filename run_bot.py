#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

_CHILD_ENV_VAR = "BUDUCCA_RUN_BOT_CHILD"
_RESTART_AFTER_SECONDS = 60.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Telegram + OpenAI-compatible bot")
    parser.add_argument(
        "--config",
        default="config",
        help="Path to the bot configuration file or directory",
    )
    return parser.parse_args(argv)


def _run_bot(config_path: str) -> None:
    from messaging_llm_bot import BotRunner, load_config

    config = load_config(config_path)

    configured_level = getattr(logging, config.runtime.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=logging.DEBUG if config.runtime.debug else configured_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    runner = BotRunner(config)
    runner.run_forever()


def _configure_supervisor_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _was_killed(returncode: int) -> bool:
    return returncode in {-signal.SIGKILL, 128 + signal.SIGKILL}


def _exit_status(returncode: int) -> int:
    if returncode < 0:
        return 128 + abs(returncode)
    return returncode


def _run_supervised(
    argv: list[str],
    *,
    sleep_seconds: float = _RESTART_AFTER_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    command = [sys.executable, str(Path(__file__).resolve()), *argv]
    child_env = os.environ.copy()
    child_env[_CHILD_ENV_VAR] = "1"

    while True:
        process = subprocess.Popen(command, env=child_env)
        try:
            returncode = process.wait()
        except KeyboardInterrupt:
            logging.info("Bot supervisor interrupted. Exiting.")
            return _exit_status(process.wait())

        if _was_killed(returncode):
            logging.error(
                "Bot process was killed with SIGKILL; restarting in %.0f seconds",
                sleep_seconds,
            )
            sleep(sleep_seconds)
            continue

        return _exit_status(returncode)


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    if os.environ.get(_CHILD_ENV_VAR) == "1":
        args = parse_args(effective_argv)
        _run_bot(args.config)
        return 0

    _configure_supervisor_logging()
    return _run_supervised(effective_argv)


if __name__ == "__main__":
    raise SystemExit(main())
