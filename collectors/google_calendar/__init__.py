from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from assistant_framework.collector_shell import run_command
from assistant_framework.workspace import Workspace

NAME = "google_calendar"
INTERVAL_SECONDS = 600


def _month_key(iso_str: str) -> str:
    try:
        return iso_str[:7]
    except Exception:
        return "unknown-month"


def create_collector(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 90))
    accounts = config.get("accounts", [])
    command_template = config.get("command_template") or os.environ.get(
        "GOOGLE_CALENDAR_COMMAND_TEMPLATE",
        "google-agentic calendar events --account {account} --format json --time-min {month_start} --time-max {month_end}",
    )

    def _run(workspace: Workspace) -> None:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        if now.month == 12:
            month_end = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            month_end = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        month_end_iso = month_end.isoformat()

        for account in accounts:
            command = command_template.format(account=account, month_start=month_start, month_end=month_end_iso)
            code, stdout, _ = run_command(command, timeout_seconds=timeout)
            if code != 0 or not stdout.strip():
                continue

            parsed = json.loads(stdout)
            events = parsed if isinstance(parsed, list) else parsed.get("events", [])
            month = _month_key(month_start)
            output_path = f"google_calendar/{account}.{month}.events.jsonl"
            lines = []
            for event in events:
                lines.append(
                    json.dumps(
                        {
                            "source": "google_calendar",
                            "collected_at": now.isoformat(),
                            "account": account,
                            "month": month,
                            **event,
                        },
                        ensure_ascii=False,
                    )
                )
            workspace.write_text(output_path, "\n".join(lines) + ("\n" if lines else ""))

    return {"name": NAME, "interval_seconds": interval, "run": _run}
