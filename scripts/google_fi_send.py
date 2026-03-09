#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Queue an outgoing Google Fi message for an external transport bridge. "
            "This does not send directly to Google Fi; it writes to workspace/google_fi_outbox.jsonl."
        )
    )
    parser.add_argument("--recipient", required=True, help="Conversation/thread identifier or phone number.")
    parser.add_argument("--message", required=True, help="Message text to send.")
    parser.add_argument("--workspace", default="workspace", help="Workspace directory (default: workspace).")
    parser.add_argument(
        "--outbox-file",
        default="google_fi_outbox.jsonl",
        help="Outbox filename under workspace (default: google_fi_outbox.jsonl).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    outbox_path = workspace / args.outbox_file
    payload = {
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "backend": "google_fi",
        "recipient": args.recipient,
        "message": args.message,
    }
    with outbox_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    print(json.dumps({"ok": True, "queued": 1, "outbox": str(outbox_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
