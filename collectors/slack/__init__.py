from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from assistant_framework.collector_shell import run_command
from assistant_framework.ingestion import append_normalized_records, normalize_collected_item, write_raw_snapshot
from assistant_framework.workspace import Workspace

NAME = "slack"
DESCRIPTION = "Collects recent Slack messages from configured exports and writes unseen items into workspace/slack.recent."
INTERVAL_SECONDS = 120
STATE_FILE = "collectors/slack.state.json"
OUTPUT_FILE = "slack.recent"
FILE_STRUCTURE = ["collectors/slack/__init__.py", "collectors/slack/README.md"]
GENERATED_FILES = [OUTPUT_FILE, STATE_FILE]


def register_collector(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 60))
    default_command = config.get("command") or os.environ.get("SLACK_EXPORT_COMMAND", "")
    accounts = config.get("accounts") or [{"name": config.get("account_name", "default"), "command": default_command}]

    def _run(workspace: Workspace) -> None:
        state = json.loads(workspace.read_text(STATE_FILE, default='{"accounts": {}}'))
        account_state = state.setdefault("accounts", {})
        now = datetime.now(timezone.utc).isoformat()
        out = []
        normalized_records = []

        for account in accounts:
            account_name = str(account.get("name") or "default")
            command = account.get("command") or default_command
            if not command:
                continue
            last_ts = str(account_state.get(account_name, {}).get("last_ts", "0"))

            code, stdout, _ = run_command(command, timeout_seconds=timeout)
            if code != 0 or not stdout.strip():
                continue

            payload = json.loads(stdout)
            write_raw_snapshot(workspace, NAME, payload)
            messages = payload if isinstance(payload, list) else payload.get("messages", [])
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
                        "collector": NAME,
                        "account": account_name,
                        "collected_at": now,
                        "channel": message.get("channel"),
                        "user": message.get("user"),
                        "ts": ts,
                        "text": message.get("text"),
                    }
                )
                normalized_records.append(
                    normalize_collected_item(
                        source="slack",
                        timestamp=now,
                        title=str(message.get("channel") or ts),
                        text=str(message.get("text") or ""),
                        metadata={"account": account_name, "channel": message.get("channel"), "user": message.get("user"), "ts": ts},
                    )
                )
            account_state[account_name] = {"last_ts": max_ts}

        if out:
            workspace.write_text(OUTPUT_FILE, "\n".join(json.dumps(i, ensure_ascii=False) for i in out) + "\n")
        append_normalized_records(workspace, NAME, normalized_records)
        workspace.write_text(STATE_FILE, json.dumps(state))

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "interval_seconds": interval,
        "generated_files": GENERATED_FILES,
        "file_structure": FILE_STRUCTURE,
        "run": _run,
    }


def create_collector(config: dict):
    return register_collector(config)
