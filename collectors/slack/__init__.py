from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from assistant_framework.collector_shell import run_command
from assistant_framework.workspace import Workspace

NAME = "slack"
INTERVAL_SECONDS = 120
STATE_FILE = "collectors/slack.state.json"
OUTPUT_FILE = "slack.recent"


def create_collector(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 60))
    command = config.get("command") or os.environ.get("SLACK_EXPORT_COMMAND", "")

    def _run(workspace: Workspace) -> None:
        if not command:
            return
        state = json.loads(workspace.read_text(STATE_FILE, default='{"last_ts": "0"}'))
        last_ts = str(state.get("last_ts", "0"))

        code, stdout, _ = run_command(command, timeout_seconds=timeout)
        if code != 0 or not stdout.strip():
            return

        payload = json.loads(stdout)
        messages = payload if isinstance(payload, list) else payload.get("messages", [])
        now = datetime.now(timezone.utc).isoformat()
        out = []
        max_ts = last_ts
        for message in messages:
            ts = str(message.get("ts", "0"))
            if ts <= last_ts:
                continue
            if ts > max_ts:
                max_ts = ts
            out.append(
                {
                    "source": "slack",
                    "received_at": now,
                    "channel": message.get("channel"),
                    "user": message.get("user"),
                    "ts": ts,
                    "text": message.get("text"),
                }
            )

        if out:
            workspace.write_text(OUTPUT_FILE, "\n".join(json.dumps(i, ensure_ascii=False) for i in out) + "\n")
        workspace.write_text(STATE_FILE, json.dumps({"last_ts": max_ts}))

    return {"name": NAME, "interval_seconds": interval, "run": _run}
