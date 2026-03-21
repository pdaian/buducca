from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any


DEFAULT_ANDROID_INBOX = "data/android-events.jsonl"
DEFAULT_STATE_FILE = "data/termux-notifications-state.json"


class TermuxNotificationCollectorError(RuntimeError):
    """Raised when the Termux notification collector cannot proceed."""


def _state_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_seen_keys(path: Path) -> set[str]:
    payload = _state_payload(path)
    seen = payload.get("active_keys")
    if not isinstance(seen, list):
        return set()
    return {item for item in seen if isinstance(item, str) and item}


def _save_seen_keys(path: Path, keys: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"active_keys": sorted(keys)}
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            rendered = str(value).strip()
            if rendered:
                return rendered
    return None


def _coerce_timestamp(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)):
        scale = 1000 if value > 10_000_000_000 else 1
        dt = datetime.fromtimestamp(value / scale, tz=timezone.utc).astimezone()
        return dt.isoformat(timespec="seconds")
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _notification_key(item: dict[str, Any]) -> str:
    parts = [
        _first_text(item.get("packageName"), item.get("package")) or "",
        _first_text(item.get("id"), item.get("notificationId"), item.get("key")) or "",
        _first_text(item.get("title")) or "",
        _first_text(item.get("content"), item.get("text"), item.get("body")) or "",
        _first_text(item.get("when"), item.get("timestamp"), item.get("postTime")) or "",
    ]
    return "\n".join(parts)


def _normalize_notification(item: dict[str, Any]) -> dict[str, Any] | None:
    package_name = _first_text(item.get("packageName"), item.get("package"))
    title = _first_text(item.get("title"))
    body = _first_text(item.get("content"), item.get("text"), item.get("body"))
    app_name = _first_text(item.get("applicationLabel"), item.get("appName"), item.get("app_label"), package_name)

    if not package_name and not title and not body:
        return None

    return {
        "type": "notification",
        "package_name": package_name,
        "app_name": app_name,
        "title": title,
        "body": body,
        "timestamp": _coerce_timestamp(item.get("when") or item.get("timestamp") or item.get("postTime")),
    }


def _notification_payloads(stdout: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise TermuxNotificationCollectorError("termux-notification-list returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise TermuxNotificationCollectorError("termux-notification-list must return a JSON array")
    return [item for item in payload if isinstance(item, dict)]


def _fetch_notifications(command: str) -> list[dict[str, Any]]:
    if which(command) is None:
        raise TermuxNotificationCollectorError(f"notification collector failed: executable {command!r} was not found in PATH")
    proc = subprocess.run([command], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or proc.stdout.strip() or "no stderr"
        raise TermuxNotificationCollectorError(f"notification collector failed: {stderr}")
    return _notification_payloads(proc.stdout)


def collect_once(
    *,
    inbox_path: Path,
    state_path: Path,
    notification_command: str,
    include_packages: set[str] | None = None,
) -> int:
    previous_keys = _load_seen_keys(state_path)
    current_keys: set[str] = set()
    events: list[dict[str, Any]] = []

    for item in _fetch_notifications(notification_command):
        package_name = _first_text(item.get("packageName"), item.get("package"))
        if include_packages and package_name not in include_packages:
            continue
        key = _notification_key(item)
        if not key:
            continue
        current_keys.add(key)
        if key in previous_keys:
            continue
        normalized = _normalize_notification(item)
        if normalized:
            events.append(normalized)

    if events:
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        with inbox_path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    _save_seen_keys(state_path, current_keys)
    return len(events)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="termux-notification-collector",
        description="Poll Termux notifications and append new entries into the Android JSONL inbox",
    )
    parser.add_argument("--inbox", default=DEFAULT_ANDROID_INBOX)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--notification-command", default="termux-notification-list")
    parser.add_argument("--include-package", action="append", default=[])

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("once", help="Collect notifications once")

    run = subparsers.add_parser("run", help="Continuously poll and append notifications")
    run.add_argument("--interval-seconds", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    include_packages = {value.strip() for value in args.include_package if value and value.strip()} or None
    try:
        if args.command == "once":
            collect_once(
                inbox_path=Path(args.inbox),
                state_path=Path(args.state_file),
                notification_command=args.notification_command,
                include_packages=include_packages,
            )
            return 0
        if args.command == "run":
            if args.interval_seconds <= 0:
                raise TermuxNotificationCollectorError("--interval-seconds must be > 0")
            while True:
                collect_once(
                    inbox_path=Path(args.inbox),
                    state_path=Path(args.state_file),
                    notification_command=args.notification_command,
                    include_packages=include_packages,
                )
                time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        return 0
    except TermuxNotificationCollectorError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
