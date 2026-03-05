from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from assistant_framework.collector_shell import run_command
from assistant_framework.workspace import Workspace

NAME = "signal_messages"
INTERVAL_SECONDS = 120
STATE_FILE = "collectors/signal_messages.state.json"
OUTPUT_FILE = "signal.messages.recent"


def _default_command(device_name: str) -> list[str]:
    return ["signal-cli", "-o", "json", "-a", device_name, "receive", "--ignore-attachments"]


def create_collector(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 60))
    device_name = str(config.get("device_name") or os.environ.get("SIGNAL_DEVICE_NAME", ""))
    command = config.get("command") or _default_command(device_name)

    def _run(workspace: Workspace) -> None:
        if not device_name:
            return

        state = json.loads(workspace.read_text(STATE_FILE, default='{"last_ts": 0}'))
        last_ts = int(state.get("last_ts", 0))

        code, stdout, _ = run_command(command, timeout_seconds=timeout)
        if code != 0 or not stdout.strip():
            return

        now = datetime.now(timezone.utc).isoformat()
        lines = []
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
                    "timestamp": timestamp,
                    "sender": envelope.get("source"),
                    "text": text,
                }
            )

        if lines:
            workspace.write_text(OUTPUT_FILE, "\n".join(json.dumps(i, ensure_ascii=False) for i in lines) + "\n")
        workspace.write_text(STATE_FILE, json.dumps({"last_ts": max_ts}))

    return {"name": NAME, "interval_seconds": interval, "run": _run}


def signup(config: dict) -> int:
    timeout = float(config.get("timeout_seconds", 120))
    qr_output = config.get("qr_output", "workspace/collectors/signal_qr.txt")
    link_command = config.get("link_command") or ["signal-cli", "link", "-n", str(config.get("device_name", "buducca"))]
    code, stdout, stderr = run_command(link_command, timeout_seconds=timeout)
    output = stdout or stderr
    Path(qr_output).parent.mkdir(parents=True, exist_ok=True)
    Path(qr_output).write_text(output, encoding="utf-8")
    return code
