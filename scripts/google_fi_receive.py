#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Emit pending Google Fi events for BUDUCCA. "
            "Reads workspace/google_fi_inbox.json written by an external bridge and prints JSON payload."
        )
    )
    parser.add_argument("--workspace", default="workspace", help="Workspace directory (default: workspace).")
    parser.add_argument(
        "--inbox-file",
        default="google_fi_inbox.json",
        help="Inbox filename under workspace (default: google_fi_inbox.json).",
    )
    parser.add_argument(
        "--no-truncate",
        action="store_true",
        help="Do not truncate inbox after read.",
    )
    return parser.parse_args()


def _empty_payload() -> dict[str, list[dict[str, Any]]]:
    return {"messages": [], "calls": []}


def _coerce_payload(value: Any) -> dict[str, list[dict[str, Any]]]:
    if isinstance(value, list):
        return {"messages": [item for item in value if isinstance(item, dict)], "calls": []}
    if not isinstance(value, dict):
        return _empty_payload()

    messages = value.get("messages", [])
    calls = value.get("calls", [])
    if not isinstance(messages, list):
        messages = []
    if not isinstance(calls, list):
        calls = []

    return {
        "messages": [item for item in messages if isinstance(item, dict)],
        "calls": [item for item in calls if isinstance(item, dict)],
    }


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    inbox_path = workspace / args.inbox_file
    if not inbox_path.exists():
        print(json.dumps(_empty_payload(), ensure_ascii=False))
        return 0

    try:
        raw = json.loads(inbox_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(json.dumps(_empty_payload(), ensure_ascii=False))
        return 0

    payload = _coerce_payload(raw)
    print(json.dumps(payload, ensure_ascii=False))

    if not args.no_truncate:
        inbox_path.write_text(json.dumps(_empty_payload(), ensure_ascii=False) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
