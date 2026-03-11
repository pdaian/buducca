from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from assistant_framework.config_files import load_json_path


def _load_whatsapp_commands(config_path: str) -> tuple[list[str], list[str]]:
    config_target = Path(config_path)
    if config_target.is_dir():
        whatsapp_path = config_target / "whatsapp.json"
    else:
        whatsapp_path = config_target
    raw = load_json_path(whatsapp_path)
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid WhatsApp config: {whatsapp_path}")
    receive_command = raw.get("receive_command")
    send_command = raw.get("send_command")
    if not isinstance(receive_command, list) or not all(isinstance(item, str) for item in receive_command):
        raise ValueError(f"whatsapp.receive_command must be a list of strings in {whatsapp_path}")
    if not isinstance(send_command, list) or not all(isinstance(item, str) for item in send_command):
        raise ValueError(f"whatsapp.send_command must be a list of strings in {whatsapp_path}")
    return receive_command, send_command


def _render_shell(command: list[str]) -> str:
    return " ".join(json.dumps(part) if any(ch.isspace() for ch in part) else part for part in command)


def _pair_command_from_receive(receive_command: list[str]) -> list[str]:
    pair_command = list(receive_command)
    for index, part in enumerate(pair_command):
        if part == "receive":
            pair_command[index] = "pair"
            return pair_command
    raise ValueError("whatsapp.receive_command must contain the `receive` subcommand")


def _ensure_headful_pair_command(pair_command: list[str]) -> list[str]:
    if "--headful" in pair_command or "--headless" in pair_command:
        return pair_command
    return [*pair_command, "--headful"]


def _render_text_send_example(send_command: list[str]) -> list[str]:
    rendered: list[str] = []
    for index, part in enumerate(send_command):
        if "{attachment}" in part:
            if index > 0 and send_command[index - 1].startswith("--") and rendered and rendered[-1] == send_command[index - 1]:
                rendered.pop()
            continue
        rendered.append(part.replace("{recipient}", "+15550001111").replace("{message}", "test"))
    return rendered


def run_signup(config_path: str) -> int:
    receive_command, send_command = _load_whatsapp_commands(config_path)
    pair_command = _ensure_headful_pair_command(_pair_command_from_receive(receive_command))
    message = f"""WhatsApp signup is concrete in this repo.

Install the bridge dependencies once:
- `pip install playwright`
- `python3 -m playwright install chromium`

Configured commands:
- `whatsapp.receive_command`: `{json.dumps(receive_command)}`
- `whatsapp.send_command`: `{json.dumps(send_command)}`

Pair the WhatsApp account and open the signup QR in a browser window:
- `{_render_shell(pair_command)}`

On your phone:
- `WhatsApp -> Settings -> Linked Devices -> Link a Device`

After linking, test the exact bridge commands BUDUCCA will use:
- `{_render_shell(receive_command)}`
- `{_render_shell(_render_text_send_example(send_command))}`

Then start the bot:
- `python3 run_bot.py --config config`

Notes:
- This Playwright bridge automates WhatsApp Web, so it remains subject to upstream UI changes.
- The linked-device browser profile is persisted under the `--session` path in your config.
- If you want attachment sending, keep `{{attachment}}` in `send_command` and call the attach-file skill.
"""
    print(message)
    completed = subprocess.run(pair_command, check=False)
    return completed.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WhatsApp signup helper")
    parser.add_argument("--config", default="config", help="Bot config JSON file or directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(run_signup(args.config))


if __name__ == "__main__":
    main()
