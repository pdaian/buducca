from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any

DEFAULT_SYNC_DIR = Path.home() / "buducca-sync"
DEFAULT_INBOX = str(DEFAULT_SYNC_DIR / "android-events.jsonl")
DEFAULT_BRIDGE_STATE_FILE = str(DEFAULT_SYNC_DIR / "android-bridge-state.json")
DEFAULT_NOTIFICATION_STATE_FILE = str(DEFAULT_SYNC_DIR / "termux-notifications-state.json")
DEFAULT_OUTBOX = str(DEFAULT_SYNC_DIR / "android-sms-outbox.jsonl")
DEFAULT_OUTBOX_STATE_FILE = str(DEFAULT_SYNC_DIR / "android-sms-outbox-state.json")
DEFAULT_SSH_KEY = str(Path.home() / ".ssh" / "buducca_android_sync")
DEFAULT_SYNC_CONFIG_FILE = str(DEFAULT_SYNC_DIR / "client-config.json")
DEFAULT_REMOTE_HOST_ENV = "BUDUCCA_REMOTE_HOST"
DEFAULT_REMOTE_DIR_ENV = "BUDUCCA_REMOTE_DIR"
DEFAULT_REMOTE_DIR = "/srv/buducca/android"
DEFAULT_SSH_KEY_COMMENT = "buducca-android-sync"
DEFAULT_NOTIFICATION_DISMISS_COMMAND = "termux-notification-remove"
MAX_ANDROID_EVENT_LINES = 100


class ClientError(RuntimeError):
    """Raised when the Termux client cannot complete the requested operation."""


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _ensure_jsonl_file(path: Path) -> None:
    _ensure_parent(path)
    if not path.exists():
        path.touch()


def _trim_jsonl_file(path: Path, *, max_lines: int) -> None:
    if max_lines <= 0 or not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= max_lines:
        return
    path.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")


def _config_payload(path: Path) -> dict[str, Any]:
    payload = _state_payload(path)
    return payload if isinstance(payload, dict) else {}


def _state_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            rendered = str(value).strip()
            if rendered:
                return rendered
    return None


def _load_offset(path: Path) -> int:
    payload = _state_payload(path)
    offset = payload.get("offset")
    return int(offset) if isinstance(offset, int) and offset >= 0 else 0


def _save_offset(path: Path, offset: int) -> None:
    _ensure_parent(path)
    path.write_text(json.dumps({"offset": max(0, offset)}) + "\n", encoding="utf-8")


def _load_seen_keys(path: Path) -> set[str]:
    payload = _state_payload(path)
    seen = payload.get("active_keys")
    if not isinstance(seen, list):
        return set()
    return {item for item in seen if isinstance(item, str) and item}


def _save_seen_keys(path: Path, keys: set[str]) -> None:
    _ensure_parent(path)
    path.write_text(json.dumps({"active_keys": sorted(keys)}, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_sync_config(path: Path | None = None) -> dict[str, str]:
    payload = _config_payload(Path(path or DEFAULT_SYNC_CONFIG_FILE))
    remote_host = _first_text(payload.get("remote_host")) or ""
    remote_dir = _first_text(payload.get("remote_dir")) or ""
    return {"remote_host": remote_host, "remote_dir": remote_dir}


def _save_sync_config(*, remote_host: str, remote_dir: str, path: Path | None = None) -> None:
    config_path = Path(path or DEFAULT_SYNC_CONFIG_FILE)
    _ensure_parent(config_path)
    config_path.write_text(
        json.dumps(
            {
                "remote_host": remote_host.strip(),
                "remote_dir": remote_dir.strip(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _jsonl_messages(path: Path, *, start_offset: int) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    file_size = path.stat().st_size
    offset = start_offset if 0 <= start_offset <= file_size else 0
    messages: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                messages.append(payload)
        new_offset = handle.tell()
    return messages, new_offset


def _jsonl_entries_with_offsets(path: Path, *, start_offset: int) -> list[tuple[dict[str, Any], int]]:
    if not path.exists():
        return []
    file_size = path.stat().st_size
    offset = start_offset if 0 <= start_offset <= file_size else 0
    entries: list[tuple[dict[str, Any], int]] = []
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        while True:
            line = handle.readline()
            if not line:
                break
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                entries.append((payload, handle.tell()))
    return entries


def _normalize_include_packages(values: list[str] | set[str] | tuple[str, ...] | None) -> set[str] | None:
    if values is None:
        return None
    normalized = {value.strip() for value in values if isinstance(value, str) and value.strip()}
    return normalized or None


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


def _dismiss_notification(item: dict[str, Any], *, dismiss_command: str) -> None:
    notification_id = _first_text(item.get("id"), item.get("notificationId"))
    if not notification_id:
        return
    try:
        _run_command(
            [dismiss_command, notification_id],
            missing_message=f"notification dismiss failed: executable {dismiss_command!r} was not found in PATH",
            error_prefix="notification dismiss failed",
        )
    except ClientError as exc:
        print(str(exc), file=sys.stderr)


def _run_command(command: list[str], *, missing_message: str, error_prefix: str) -> subprocess.CompletedProcess[str]:
    executable = command[0]
    if "/" not in executable and which(executable) is None:
        raise ClientError(missing_message)
    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ClientError(missing_message) from exc
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or proc.stdout.strip() or "no stderr"
        raise ClientError(f"{error_prefix}: {stderr}")
    return proc


def collect_notifications_once(
    *,
    inbox_path: Path,
    state_path: Path,
    notification_command: str,
    notification_dismiss_command: str = DEFAULT_NOTIFICATION_DISMISS_COMMAND,
    include_packages: set[str] | None = None,
) -> int:
    include_packages = _normalize_include_packages(include_packages)
    proc = _run_command(
        [notification_command],
        missing_message=f"notification collector failed: executable {notification_command!r} was not found in PATH",
        error_prefix="notification collector failed",
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClientError("termux-notification-list returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise ClientError("termux-notification-list must return a JSON array")

    previous_keys = _load_seen_keys(state_path)
    current_keys: set[str] = set()
    events: list[dict[str, Any]] = []
    dismiss_items: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        package_name = _first_text(item.get("packageName"), item.get("package"))
        if include_packages is not None and package_name not in include_packages:
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
            dismiss_items.append(item)

    if events:
        _ensure_parent(inbox_path)
        with inbox_path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        _trim_jsonl_file(inbox_path, max_lines=MAX_ANDROID_EVENT_LINES)
    _save_seen_keys(state_path, current_keys)
    for item in dismiss_items:
        _dismiss_notification(item, dismiss_command=notification_dismiss_command)
    return len(events)


def receive_events(*, inbox_path: Path, state_path: Path) -> dict[str, list[dict[str, Any]]]:
    offset = _load_offset(state_path)
    messages, new_offset = _jsonl_messages(inbox_path, start_offset=offset)
    _save_offset(state_path, new_offset)
    return {"messages": messages}


def queue_sms(*, recipient: str, message: str, outbox_path: Path) -> None:
    _ensure_parent(outbox_path)
    with outbox_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "recipient": recipient,
                    "message": message,
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def send_sms(*, recipient: str, message: str, sms_command: str) -> None:
    _run_command(
        [sms_command, "-n", recipient, message],
        missing_message=f"Android SMS send failed: executable {sms_command!r} was not found in PATH",
        error_prefix="Android SMS send failed",
    )


def flush_sms_outbox(*, outbox_path: Path, state_path: Path, sms_command: str) -> int:
    offset = _load_offset(state_path)
    delivered = 0
    for payload, next_offset in _jsonl_entries_with_offsets(outbox_path, start_offset=offset):
        recipient = payload.get("recipient")
        message = payload.get("message")
        if not isinstance(recipient, str) or not recipient.strip():
            _save_offset(state_path, next_offset)
            continue
        if not isinstance(message, str) or not message.strip():
            _save_offset(state_path, next_offset)
            continue
        send_sms(recipient=recipient.strip(), message=message, sms_command=sms_command)
        _save_offset(state_path, next_offset)
        delivered += 1
    return delivered


def generate_ssh_key(*, private_key_path: Path, comment: str, force: bool) -> str:
    if private_key_path.exists() and not force:
        raise ClientError(f"Refusing to overwrite existing SSH key: {private_key_path}. Pass --force to replace it.")
    _ensure_parent(private_key_path)
    _run_command(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            comment,
            "-f",
            str(private_key_path),
        ],
        missing_message="Android SSH key generation failed: executable 'ssh-keygen' was not found in PATH",
        error_prefix="Android SSH key generation failed",
    )
    public_key_path = private_key_path.with_name(private_key_path.name + ".pub")
    return public_key_path.read_text(encoding="utf-8").strip()


def _ensure_ssh_key(path: Path) -> None:
    public_key_path = path.with_name(path.name + ".pub")
    if path.exists() and public_key_path.exists():
        return
    public_key = generate_ssh_key(private_key_path=path, comment=DEFAULT_SSH_KEY_COMMENT, force=True)
    print(
        json.dumps(
            {
                "generated_ssh_key": str(path),
                "public_key": public_key,
            },
            ensure_ascii=False,
        )
    )


def sync_once(
    *,
    local_inbox: Path,
    local_outbox: Path,
    remote_host: str,
    remote_dir: str,
    ssh_key: str,
    sms_command: str,
    outbox_state_path: Path,
    scp_command: str,
) -> int:
    _ensure_jsonl_file(local_inbox)
    _ensure_jsonl_file(local_outbox)
    remote_inbox = f"{remote_host}:{remote_dir.rstrip('/')}/{local_inbox.name}"
    remote_outbox = f"{remote_host}:{remote_dir.rstrip('/')}/{local_outbox.name}"
    _run_command(
        [scp_command, "-q", "-i", ssh_key, str(local_inbox), remote_inbox],
        missing_message=f"sync failed: executable {scp_command!r} was not found in PATH",
        error_prefix="sync failed",
    )
    _run_command(
        [scp_command, "-q", "-i", ssh_key, remote_outbox, str(local_outbox)],
        missing_message=f"sync failed: executable {scp_command!r} was not found in PATH",
        error_prefix="sync failed",
    )
    return flush_sms_outbox(outbox_path=local_outbox, state_path=outbox_state_path, sms_command=sms_command)


def run_client_loop(
    *,
    inbox_path: Path,
    notification_state_path: Path,
    outbox_path: Path,
    outbox_state_path: Path,
    remote_host: str,
    remote_dir: str,
    ssh_key: str,
    sms_command: str,
    notification_command: str,
    notification_dismiss_command: str,
    include_packages: set[str] | None,
    scp_command: str,
    interval_seconds: float,
) -> None:
    if interval_seconds <= 0:
        raise ClientError("--interval-seconds must be > 0")
    _ensure_jsonl_file(inbox_path)
    _ensure_jsonl_file(outbox_path)
    while True:
        collect_notifications_once(
            inbox_path=inbox_path,
            state_path=notification_state_path,
            notification_command=notification_command,
            notification_dismiss_command=notification_dismiss_command,
            include_packages=include_packages,
        )
        sync_once(
            local_inbox=inbox_path,
            local_outbox=outbox_path,
            remote_host=remote_host,
            remote_dir=remote_dir,
            ssh_key=ssh_key,
            sms_command=sms_command,
            outbox_state_path=outbox_state_path,
            scp_command=scp_command,
        )
        time.sleep(interval_seconds)


def _default_remote_host() -> str:
    env_value = os.environ.get(DEFAULT_REMOTE_HOST_ENV, "").strip()
    if env_value:
        return env_value
    return _load_sync_config()["remote_host"]


def _default_remote_dir() -> str:
    env_value = os.environ.get(DEFAULT_REMOTE_DIR_ENV, "").strip()
    if env_value:
        return env_value
    configured = _load_sync_config()["remote_dir"]
    return configured or DEFAULT_REMOTE_DIR


def _prompt_sync_target() -> tuple[str, str]:
    if not sys.stdin.isatty():
        raise ClientError(
            "Sync target is not configured. Set "
            f"{DEFAULT_REMOTE_HOST_ENV}/{DEFAULT_REMOTE_DIR_ENV}, pass --remote-host, or rerun interactively."
        )
    current_dir = _default_remote_dir()
    remote_host = input("Remote host (for example user@example.com): ").strip()
    if not remote_host:
        raise ClientError("Remote host is required.")
    remote_dir = input(f"Remote directory [{current_dir}]: ").strip() or current_dir
    _save_sync_config(remote_host=remote_host, remote_dir=remote_dir)
    return remote_host, remote_dir


def _sync_target(args: argparse.Namespace) -> tuple[str, str]:
    remote_host = getattr(args, "remote_host", "").strip()
    remote_dir = getattr(args, "remote_dir", "").strip()
    if remote_host and remote_dir:
        _save_sync_config(remote_host=remote_host, remote_dir=remote_dir)
        return remote_host, remote_dir
    if remote_host:
        resolved_dir = remote_dir or _default_remote_dir()
        _save_sync_config(remote_host=remote_host, remote_dir=resolved_dir)
        return remote_host, resolved_dir
    configured_host = _default_remote_host()
    configured_dir = _default_remote_dir()
    if configured_host:
        _save_sync_config(remote_host=configured_host, remote_dir=configured_dir)
        return configured_host, configured_dir
    return _prompt_sync_target()


def _setup_summary_lines() -> list[str]:
    sync_dir = DEFAULT_SYNC_DIR
    remote_host = _default_remote_host() or "<server>"
    remote_dir = _default_remote_dir()
    ssh_key_exists = Path(DEFAULT_SSH_KEY).exists()
    return [
        "BUDUCCA Android client setup",
        "",
        "Default local files:",
        f"- inbox: {DEFAULT_INBOX}",
        f"- notification state: {DEFAULT_NOTIFICATION_STATE_FILE}",
        f"- SMS outbox: {DEFAULT_OUTBOX}",
        f"- SMS outbox state: {DEFAULT_OUTBOX_STATE_FILE}",
        f"- SSH key: {DEFAULT_SSH_KEY} ({'present' if ssh_key_exists else 'missing'})",
        f"- persisted sync config: {DEFAULT_SYNC_CONFIG_FILE}",
        "",
        "Sync target:",
        f"- export {DEFAULT_REMOTE_HOST_ENV}={remote_host}",
        f"- export {DEFAULT_REMOTE_DIR_ENV}={remote_dir}",
        "",
        "Suggested first-run sequence:",
        "1. pkg update && pkg install python termux-api openssh",
        f"2. mkdir -p {sync_dir}",
        "3. python3 scripts/run_client.py run --include-package org.thoughtcrime.securesms --include-package com.whatsapp",
        "",
        "If the SSH key or sync target is missing, the script creates or prompts for them automatically.",
    ]


def print_setup_guide() -> None:
    print("\n".join(_setup_summary_lines()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/run_client.py", description="Self-contained Termux client for BUDUCCA")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("setup", help="Print setup guidance and the default local file layout")

    receive = subparsers.add_parser("receive", help="Read new events from a JSONL inbox")
    receive.add_argument("--inbox", default=DEFAULT_INBOX)
    receive.add_argument("--state-file", default=DEFAULT_BRIDGE_STATE_FILE)

    send = subparsers.add_parser("send", help="Send an SMS or queue it into an outbox")
    send.add_argument("--recipient", required=True)
    send.add_argument("--message", required=True)
    send.add_argument("--sms-command", default="termux-sms-send")
    send.add_argument("--outbox", default="")

    flush_outbox = subparsers.add_parser("flush-outbox", help="Send queued SMS requests from a JSONL outbox")
    flush_outbox.add_argument("--outbox", default=DEFAULT_OUTBOX)
    flush_outbox.add_argument("--state-file", default=DEFAULT_OUTBOX_STATE_FILE)
    flush_outbox.add_argument("--sms-command", default="termux-sms-send")

    collect = subparsers.add_parser("collect-notifications", help="Poll Termux notifications into the inbox")
    collect.add_argument("--inbox", default=DEFAULT_INBOX)
    collect.add_argument("--state-file", default=DEFAULT_NOTIFICATION_STATE_FILE)
    collect.add_argument("--notification-command", default="termux-notification-list")
    collect.add_argument("--notification-dismiss-command", default=DEFAULT_NOTIFICATION_DISMISS_COMMAND)
    collect.add_argument("--include-package", action="append", default=[])
    collect_subparsers = collect.add_subparsers(dest="collect_command", required=True)
    collect_subparsers.add_parser("once", help="Collect notifications once")
    collect_run = collect_subparsers.add_parser("run", help="Continuously collect notifications")
    collect_run.add_argument("--interval-seconds", type=float, default=2.0)

    sync = subparsers.add_parser("sync", help="Push inbox, pull outbox, and flush SMS from the device")
    sync.add_argument("--inbox", default=DEFAULT_INBOX)
    sync.add_argument("--outbox", default=DEFAULT_OUTBOX)
    sync.add_argument("--outbox-state-file", default=DEFAULT_OUTBOX_STATE_FILE)
    sync.add_argument("--remote-host", default=_default_remote_host())
    sync.add_argument("--remote-dir", default=_default_remote_dir())
    sync.add_argument("--ssh-key", default=DEFAULT_SSH_KEY)
    sync.add_argument("--sms-command", default="termux-sms-send")
    sync.add_argument("--scp-command", default="scp")
    sync_subparsers = sync.add_subparsers(dest="sync_command", required=True)
    sync_subparsers.add_parser("once", help="Run one sync pass")
    sync_run = sync_subparsers.add_parser("run", help="Continuously sync and flush SMS")
    sync_run.add_argument("--interval-seconds", type=float, default=2.0)

    run = subparsers.add_parser("run", help="Collect notifications, sync files, and flush SMS in one loop")
    run.add_argument("--inbox", default=DEFAULT_INBOX)
    run.add_argument("--notification-state-file", default=DEFAULT_NOTIFICATION_STATE_FILE)
    run.add_argument("--outbox", default=DEFAULT_OUTBOX)
    run.add_argument("--outbox-state-file", default=DEFAULT_OUTBOX_STATE_FILE)
    run.add_argument("--remote-host", default=_default_remote_host())
    run.add_argument("--remote-dir", default=_default_remote_dir())
    run.add_argument("--ssh-key", default=DEFAULT_SSH_KEY)
    run.add_argument("--sms-command", default="termux-sms-send")
    run.add_argument("--notification-command", default="termux-notification-list")
    run.add_argument("--notification-dismiss-command", default=DEFAULT_NOTIFICATION_DISMISS_COMMAND)
    run.add_argument("--include-package", action="append", default=[])
    run.add_argument("--scp-command", default="scp")
    run.add_argument("--interval-seconds", type=float, default=2.0)

    generate_key = subparsers.add_parser("generate-ssh-key", help="Generate an SSH key for Android sync")
    generate_key.add_argument("--private-key", default=DEFAULT_SSH_KEY)
    generate_key.add_argument("--comment", default="buducca-android-sync")
    generate_key.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    if not effective_argv:
        effective_argv = ["setup"]
    args = build_parser().parse_args(effective_argv)
    try:
        if args.command == "setup":
            print_setup_guide()
            return 0
        if args.command == "receive":
            print(json.dumps(receive_events(inbox_path=Path(args.inbox), state_path=Path(args.state_file)), ensure_ascii=False))
            return 0
        if args.command == "send":
            if args.outbox:
                queue_sms(recipient=args.recipient, message=args.message, outbox_path=Path(args.outbox))
            else:
                send_sms(recipient=args.recipient, message=args.message, sms_command=args.sms_command)
            return 0
        if args.command == "flush-outbox":
            delivered = flush_sms_outbox(
                outbox_path=Path(args.outbox),
                state_path=Path(args.state_file),
                sms_command=args.sms_command,
            )
            print(json.dumps({"delivered": delivered}, ensure_ascii=False))
            return 0
        if args.command == "collect-notifications":
            include_packages = _normalize_include_packages(args.include_package)
            if args.collect_command == "once":
                appended = collect_notifications_once(
                    inbox_path=Path(args.inbox),
                    state_path=Path(args.state_file),
                    notification_command=args.notification_command,
                    notification_dismiss_command=args.notification_dismiss_command,
                    include_packages=include_packages,
                )
                print(json.dumps({"appended": appended}, ensure_ascii=False))
                return 0
            if args.interval_seconds <= 0:
                raise ClientError("--interval-seconds must be > 0")
            while True:
                appended = collect_notifications_once(
                    inbox_path=Path(args.inbox),
                    state_path=Path(args.state_file),
                    notification_command=args.notification_command,
                    notification_dismiss_command=args.notification_dismiss_command,
                    include_packages=include_packages,
                )
                print(json.dumps({"appended": appended}, ensure_ascii=False))
                sys.stdout.flush()
                time.sleep(args.interval_seconds)
        if args.command == "sync":
            _ensure_ssh_key(Path(args.ssh_key))
            if args.sync_command == "once":
                remote_host, remote_dir = _sync_target(args)
                delivered = sync_once(
                    local_inbox=Path(args.inbox),
                    local_outbox=Path(args.outbox),
                    remote_host=remote_host,
                    remote_dir=remote_dir,
                    ssh_key=args.ssh_key,
                    sms_command=args.sms_command,
                    outbox_state_path=Path(args.outbox_state_file),
                    scp_command=args.scp_command,
                )
                print(json.dumps({"delivered": delivered}, ensure_ascii=False))
                return 0
            if args.interval_seconds <= 0:
                raise ClientError("--interval-seconds must be > 0")
            while True:
                remote_host, remote_dir = _sync_target(args)
                delivered = sync_once(
                    local_inbox=Path(args.inbox),
                    local_outbox=Path(args.outbox),
                    remote_host=remote_host,
                    remote_dir=remote_dir,
                    ssh_key=args.ssh_key,
                    sms_command=args.sms_command,
                    outbox_state_path=Path(args.outbox_state_file),
                    scp_command=args.scp_command,
                )
                print(json.dumps({"delivered": delivered}, ensure_ascii=False))
                sys.stdout.flush()
                time.sleep(args.interval_seconds)
        if args.command == "run":
            _ensure_ssh_key(Path(args.ssh_key))
            remote_host, remote_dir = _sync_target(args)
            run_client_loop(
                inbox_path=Path(args.inbox),
                notification_state_path=Path(args.notification_state_file),
                outbox_path=Path(args.outbox),
                outbox_state_path=Path(args.outbox_state_file),
                remote_host=remote_host,
                remote_dir=remote_dir,
                ssh_key=args.ssh_key,
                sms_command=args.sms_command,
                notification_command=args.notification_command,
                notification_dismiss_command=args.notification_dismiss_command,
                include_packages=_normalize_include_packages(args.include_package),
                scp_command=args.scp_command,
                interval_seconds=args.interval_seconds,
            )
            return 0
        if args.command == "generate-ssh-key":
            print(generate_ssh_key(private_key_path=Path(args.private_key), comment=args.comment, force=bool(args.force)))
            return 0
    except KeyboardInterrupt:
        return 0
    except ClientError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
