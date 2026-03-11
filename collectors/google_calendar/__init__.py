from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from assistant_framework.collector_shell import run_command
from assistant_framework.ingestion import append_normalized_records, normalize_collected_item, write_raw_snapshot
from assistant_framework.workspace import Workspace

NAME = "google_calendar"
DESCRIPTION = "Collects Google Calendar events for the current month and writes one JSONL file per account."
INTERVAL_SECONDS = 600
FILE_STRUCTURE = [
    "collectors/google_calendar/__init__.py",
    "collectors/google_calendar/README.md",
    "collectors/google_calendar/google_calendar_api.py",
]
GENERATED_FILES = ["google_calendar/<account>.<YYYY-MM>.events.jsonl"]


def _month_key(iso_str: str) -> str:
    try:
        return iso_str[:7]
    except Exception:
        return "unknown-month"


def register_collector(config: dict):
    interval = float(config.get("interval_seconds", INTERVAL_SECONDS))
    timeout = float(config.get("timeout_seconds", 90))
    raw_accounts = config.get("accounts", [])
    default_script = Path(__file__).with_name("google_calendar_api.py").as_posix()
    command_template = config.get("command_template") or os.environ.get(
        "GOOGLE_CALENDAR_COMMAND_TEMPLATE",
        f"python3 {default_script} --account {{account}} --time-min {{month_start}} --time-max {{month_end}}",
    )

    def _normalize_account(account: str | dict) -> tuple[str, str]:
        if isinstance(account, dict):
            account_name = str(account.get("name") or account.get("account") or "")
            template = str(account.get("command_template") or command_template)
            return account_name, template
        return str(account), command_template

    accounts = [_normalize_account(account) for account in raw_accounts]

    def _run(workspace: Workspace) -> None:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        if now.month == 12:
            month_end = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            month_end = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        month_end_iso = month_end.isoformat()

        for account, template in accounts:
            if not account:
                continue
            command = template.format(account=account, month_start=month_start, month_end=month_end_iso)
            code, stdout, _ = run_command(command, timeout_seconds=timeout)
            if code != 0 or not stdout.strip():
                continue

            parsed = json.loads(stdout)
            write_raw_snapshot(workspace, NAME, parsed)
            events = parsed if isinstance(parsed, list) else parsed.get("events", [])
            month = _month_key(month_start)
            output_path = f"google_calendar/{account}.{month}.events.jsonl"
            lines = []
            normalized_records = []
            for event in events:
                lines.append(
                    json.dumps(
                        {
                            "source": "google_calendar",
                            "collector": NAME,
                            "collected_at": now.isoformat(),
                            "account": account,
                            "month": month,
                            **event,
                        },
                        ensure_ascii=False,
                    )
                )
                normalized_records.append(
                    normalize_collected_item(
                        source="google_calendar",
                        timestamp=now.isoformat(),
                        title=str(event.get("summary") or event.get("id") or "calendar event"),
                        text=str(event.get("description") or event.get("summary") or ""),
                        metadata={"account": account, "month": month, **event},
                    )
                )
            workspace.write_text(output_path, "\n".join(lines) + ("\n" if lines else ""))
            append_normalized_records(workspace, NAME, normalized_records)

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "interval_seconds": interval,
        "generated_files": GENERATED_FILES,
        "file_structure": FILE_STRUCTURE,
        "run": _run,
    }
