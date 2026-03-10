from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_gcsa():
    try:
        from gcsa.google_calendar import GoogleCalendar
        from gcsa.event import Event
    except ImportError as exc:
        raise RuntimeError(
            "google_calendar_api requires the 'gcsa' package. "
            "Install it with: pip install gcsa"
        ) from exc
    return GoogleCalendar, Event


def _serialize_event(event: Any) -> dict[str, Any]:
    start = getattr(event, "start", None)
    end = getattr(event, "end", None)
    created = getattr(event, "created", None)
    updated = getattr(event, "updated", None)
    return {
        "id": getattr(event, "event_id", None) or getattr(event, "id", None),
        "summary": getattr(event, "summary", None),
        "description": getattr(event, "description", None),
        "location": getattr(event, "location", None),
        "status": getattr(event, "status", None),
        "start": start.isoformat() if hasattr(start, "isoformat") else None,
        "end": end.isoformat() if hasattr(end, "isoformat") else None,
        "created": created.isoformat() if hasattr(created, "isoformat") else None,
        "updated": updated.isoformat() if hasattr(updated, "isoformat") else None,
        "html_link": getattr(event, "html_link", None),
    }


def fetch_events(
    *,
    account: str,
    time_min: str,
    time_max: str,
    credentials_path: str | None = None,
    token_path: str | None = None,
) -> list[dict[str, Any]]:
    GoogleCalendar, _ = _load_gcsa()

    kwargs: dict[str, Any] = {}
    if credentials_path:
        kwargs["credentials_path"] = credentials_path
    if token_path:
        kwargs["token_path"] = token_path

    calendar = GoogleCalendar(account, **kwargs)
    events = calendar.get_events(
        time_min=datetime.fromisoformat(time_min),
        time_max=datetime.fromisoformat(time_max),
        order_by="startTime",
        single_events=True,
    )
    return [_serialize_event(event) for event in events]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Google Calendar events as JSON.")
    parser.add_argument("--account", required=True, help="Google Calendar account or calendar id")
    parser.add_argument("--time-min", required=True, help="Inclusive ISO-8601 lower bound")
    parser.add_argument("--time-max", required=True, help="Exclusive ISO-8601 upper bound")
    parser.add_argument(
        "--credentials-path",
        default=str(Path.home() / ".config" / "buducca" / "google_calendar_credentials.json"),
        help="OAuth client credentials JSON path",
    )
    parser.add_argument(
        "--token-path",
        default=str(Path.home() / ".config" / "buducca" / "google_calendar_token.pickle"),
        help="OAuth token cache path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    events = fetch_events(
        account=args.account,
        time_min=args.time_min,
        time_max=args.time_max,
        credentials_path=args.credentials_path,
        token_path=args.token_path,
    )
    print(json.dumps(events, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
