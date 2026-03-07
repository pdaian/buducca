from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from assistant_framework.collector_shell import run_command
from assistant_framework.workspace import Workspace

NAME = "signal_messages"
INTERVAL_SECONDS = 120
STATE_FILE = "collectors/signal_messages.state.json"
OUTPUT_FILE = "signal.messages.recent"
HISTORY_FILE = "logs/signal.history"
FILE_STRUCTURE = ["collectors/signal_messages/__init__.py", "collectors/signal_messages/README.md"]
IGNORE_ATTACHMENTS_DEFAULT = True


def _default_command(device_name: str, ignore_attachments: bool = IGNORE_ATTACHMENTS_DEFAULT) -> list[str]:
    command = ["signal-cli", "-o", "json", "-a", device_name, "receive"]
    if ignore_attachments:
        command.append("--ignore-attachments")
    return command


def _collect_from_history(workspace: Workspace, state: dict, max_messages: int) -> list[dict]:
    raw_history = workspace.read_text(HISTORY_FILE, default="")
    if not raw_history.strip():
        return []

    history_pos = int(state.get("history_pos", 0))
    all_lines = raw_history.splitlines()
    if history_pos > len(all_lines):
        history_pos = 0

    new_lines = all_lines[history_pos:]
    parsed: list[dict] = []
    for raw in new_lines[-max_messages:]:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("backend") != "signal":
            continue
        parsed.append(
            {
                "received_at": event.get("logged_at"),
                "source": "frontend_log",
                "account": "default",
                "direction": event.get("direction"),
                "conversation_id": event.get("conversation_id"),
                "sender": event.get("sender_id"),
                "text": event.get("text"),
            }
        )

    state["history_pos"] = len(all_lines)
    return parsed


def create_collector(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 60))
    max_messages = int(config.get("max_messages", 50))
    device_name = str(config.get("device_name") or os.environ.get("SIGNAL_DEVICE_NAME", ""))
    ignore_attachments = bool(config.get("ignore_attachments", IGNORE_ATTACHMENTS_DEFAULT))
    command = config.get("command") or _default_command(device_name, ignore_attachments=ignore_attachments)
    accounts = config.get("accounts") or [{"name": config.get("account_name", "default"), "device_name": device_name, "command": command}]

    def _run(workspace: Workspace) -> None:
        state = json.loads(workspace.read_text(STATE_FILE, default='{"accounts": {}, "history_pos": 0}'))
        state.setdefault("history_pos", 0)

        history_lines = _collect_from_history(workspace, state, max_messages=max_messages)

        account_state = state.setdefault("accounts", {})
        now = datetime.now(timezone.utc).isoformat()
        lines = list(history_lines)

        for account in accounts:
            account_name = str(account.get("name") or "default")
            account_device = str(account.get("device_name") or "")
            account_ignore_attachments = account.get("ignore_attachments")
            if account_ignore_attachments is None:
                account_ignore_attachments = ignore_attachments
            account_command = account.get("command") or _default_command(
                account_device,
                ignore_attachments=bool(account_ignore_attachments),
            )
            if not account_device:
                continue

            last_ts = int(account_state.get(account_name, {}).get("last_ts", 0))
            code, stdout, _ = run_command(account_command, timeout_seconds=timeout)
            if code != 0 or not stdout.strip():
                continue

            max_ts = last_ts
            for raw in stdout.splitlines():
                if not raw.strip():
                    continue
                payload = json.loads(raw)
                envelope = payload.get("envelope") or {}
                data_message = envelope.get("dataMessage") or {}
                text = data_message.get("message")
                timestamp = int(envelope.get("timestamp", 0))
                if not text or timestamp <= last_ts:
                    continue
                max_ts = max(max_ts, timestamp)
                lines.append(
                    {
                        "received_at": now,
                        "source": "signal",
                        "account": account_name,
                        "timestamp": timestamp,
                        "sender": envelope.get("source"),
                        "text": text,
                    }
                )
            account_state[account_name] = {"last_ts": max_ts}

        if lines:
            workspace.write_text(OUTPUT_FILE, "\n".join(json.dumps(i, ensure_ascii=False) for i in lines) + "\n")
        workspace.write_text(STATE_FILE, json.dumps(state))

    return {"name": NAME, "interval_seconds": interval, "run": _run}


def signup(config: dict) -> int:
    _ = config
    print(
        "Signal signup is no longer automated by this collector.\n"
        "Set up signal-cli with your preferred method (phone number or QR linked-device flow), then rerun collectors.\n"
        "Docs: https://github.com/AsamK/signal-cli/wiki and "
        "https://github.com/AsamK/signal-cli/wiki/Registration-with-signal-cli"
    )
    return 0
