from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from assistant_framework.collector_shell import run_command
from assistant_framework.workspace import Workspace

NAME = "whatsapp_messages"
INTERVAL_SECONDS = 120
STATE_FILE = "collectors/whatsapp_messages.state.json"
OUTPUT_FILE = "whatsapp.messages.recent"
FILE_STRUCTURE = ["collectors/whatsapp_messages/__init__.py", "collectors/whatsapp_messages/README.md"]


def create_collector(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 90))
    default_session_file = str(config.get("session_file") or os.environ.get("WHATSAPP_SESSION_FILE", ""))
    default_command = config.get("command") or os.environ.get("WHATSAPP_EXPORT_COMMAND", "")
    accounts = config.get("accounts") or [
        {
            "name": config.get("account_name", "default"),
            "session_file": default_session_file,
            "command": default_command,
        }
    ]

    def _run(workspace: Workspace) -> None:
        state = json.loads(workspace.read_text(STATE_FILE, default='{"accounts": {}}'))
        account_state = state.setdefault("accounts", {})
        now = datetime.now(timezone.utc).isoformat()
        out = []

        for account in accounts:
            account_name = str(account.get("name") or "default")
            session_file = str(account.get("session_file") or default_session_file)
            command = account.get("command") or default_command
            if not session_file or not Path(session_file).exists() or not command:
                continue

            last_ts = int(account_state.get(account_name, {}).get("last_ts", 0))
            code, stdout, _ = run_command(command, timeout_seconds=timeout)
            if code != 0 or not stdout.strip():
                continue

            payload = json.loads(stdout)
            messages = payload if isinstance(payload, list) else payload.get("messages", [])

            max_ts = last_ts
            for message in messages:
                ts = int(message.get("timestamp", 0))
                if ts <= last_ts:
                    continue
                max_ts = max(max_ts, ts)
                out.append({"source": "whatsapp", "account": account_name, "received_at": now, **message})
            account_state[account_name] = {"last_ts": max_ts}

        if out:
            workspace.write_text(OUTPUT_FILE, "\n".join(json.dumps(i, ensure_ascii=False) for i in out) + "\n")
        workspace.write_text(STATE_FILE, json.dumps(state))

    return {"name": NAME, "interval_seconds": interval, "run": _run}


def signup(config: dict) -> int:
    timeout = float(config.get("timeout_seconds", 120))
    qr_command = config.get("qr_command") or os.environ.get("WHATSAPP_QR_COMMAND", "")
    qr_output = config.get("qr_output", "workspace/collectors/whatsapp_qr.txt")
    if not qr_command:
        return 1
    code, stdout, stderr = run_command(qr_command, timeout_seconds=timeout)
    Path(qr_output).parent.mkdir(parents=True, exist_ok=True)
    Path(qr_output).write_text(stdout or stderr, encoding="utf-8")
    return code
