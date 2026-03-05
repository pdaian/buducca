from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from assistant_framework.collector_shell import run_command
from assistant_framework.workspace import Workspace

NAME = "gmail"
INTERVAL_SECONDS = 300
STATE_FILE = "collectors/gmail.state.json"
OUTPUT_FILE = "gmail.recent"


def create_collector(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 90))
    command = config.get("command") or os.environ.get(
        "GMAIL_AGENTIC_COMMAND",
        "google-agentic gmail list --format json --limit 50",
    )

    def _run(workspace: Workspace) -> None:
        state = json.loads(workspace.read_text(STATE_FILE, default='{"seen_ids": []}'))
        seen_ids = set(state.get("seen_ids", []))

        code, stdout, _ = run_command(command, timeout_seconds=timeout)
        if code != 0 or not stdout.strip():
            return

        parsed = json.loads(stdout)
        messages = parsed if isinstance(parsed, list) else parsed.get("messages", [])

        now = datetime.now(timezone.utc).isoformat()
        out = []
        for item in messages:
            msg_id = str(item.get("id", ""))
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            out.append(
                {
                    "source": "gmail",
                    "received_at": now,
                    "id": msg_id,
                    "thread_id": item.get("threadId"),
                    "from": item.get("from"),
                    "subject": item.get("subject"),
                    "snippet": item.get("snippet"),
                    "date": item.get("date"),
                }
            )

        if out:
            workspace.write_text(OUTPUT_FILE, "\n".join(json.dumps(i, ensure_ascii=False) for i in out) + "\n")
        workspace.write_text(STATE_FILE, json.dumps({"seen_ids": sorted(seen_ids)[-2000:]}))

    return {"name": NAME, "interval_seconds": interval, "run": _run}
