from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any

from .interfaces import IncomingMessage


DEFAULT_ANDROID_INBOX = "data/android-events.jsonl"
DEFAULT_ANDROID_STATE_FILE = "data/android-bridge-state.json"
DEFAULT_ANDROID_OUTBOX = "data/android-sms-outbox.jsonl"
DEFAULT_ANDROID_OUTBOX_STATE_FILE = "data/android-sms-outbox-state.json"
DEFAULT_ANDROID_SSH_KEY = "data/android-sync-ed25519"


class AndroidFrontendUnavailableError(RuntimeError):
    """Raised when the Android frontend is not runnable in the current environment."""


class AndroidClient:
    def __init__(self, receive_command: list[str], send_command: list[str]) -> None:
        self.receive_command = receive_command
        self.send_command = send_command
        self._update_counter = 0

    def _validate(self, command: list[str], *, name: str) -> None:
        if not command:
            raise AndroidFrontendUnavailableError(f"Android frontend disabled: {name} command is empty")
        executable = command[0]
        if "/" not in executable and which(executable) is None:
            raise AndroidFrontendUnavailableError(
                f"Android frontend disabled: executable {executable!r} was not found in PATH"
            )

    def get_updates(self) -> list[IncomingMessage]:
        self._validate(self.receive_command, name="receive")
        try:
            proc = subprocess.run(self.receive_command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise AndroidFrontendUnavailableError(
                f"Android frontend disabled: executable {exc.filename!r} was not found"
            ) from exc
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or proc.stdout.strip() or "no stderr"
            raise RuntimeError(f"Android receive command failed: {stderr}")
        return self._parse_updates(proc.stdout)

    def send_message(self, recipient: str, text: str) -> None:
        self._validate(self.send_command, name="send")
        command = [part.replace("{recipient}", recipient).replace("{message}", text) for part in self.send_command]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise AndroidFrontendUnavailableError(
                f"Android frontend disabled: executable {exc.filename!r} was not found"
            ) from exc
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or proc.stdout.strip() or "no stderr"
            raise RuntimeError(f"Android send command failed: {stderr}")

    def _parse_updates(self, stdout: str) -> list[IncomingMessage]:
        text = stdout.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Android receive command must output valid JSON") from exc

        if isinstance(payload, dict):
            raw_messages = payload.get("messages", [])
        elif isinstance(payload, list):
            raw_messages = payload
        else:
            return []

        updates: list[IncomingMessage] = []
        for item in raw_messages:
            parsed = self._parse_message(item)
            if parsed:
                updates.append(parsed)
        return updates

    def _parse_message(self, item: Any) -> IncomingMessage | None:
        if not isinstance(item, dict):
            return None
        channel = self._first_text(item.get("channel"), item.get("type"), item.get("event_type")) or "message"
        text_value = self._first_text(item.get("text"), item.get("body"), item.get("message"), item.get("content"))
        conversation_id = self._first_text(item.get("conversation_id"), item.get("thread_id"), item.get("chat_id"))
        sender_id = self._first_text(item.get("sender_id"), item.get("from"), item.get("address"), item.get("sender"))
        sender_name = self._first_text(item.get("sender_name"), item.get("title"), item.get("app_name"), item.get("name"))
        sender_contact = self._first_text(item.get("sender_contact"), sender_name, sender_id)
        sent_at = self._first_text(item.get("sent_at"), item.get("timestamp"), item.get("posted_at"))
        event_id = self._first_text(item.get("event_id"), item.get("id"))

        if channel == "notification":
            package_name = self._first_text(item.get("package_name"), item.get("package"))
            title = self._first_text(item.get("title"))
            body = self._first_text(item.get("body"), item.get("text"), item.get("message"))
            text_value = self._compose_notification_text(title=title, body=body, package_name=package_name)
            sender_id = sender_id or (f"notif:{package_name}" if package_name else "notif:android")
            conversation_id = conversation_id or sender_id
            sender_name = sender_name or package_name or "Android notification"
            sender_contact = sender_contact or sender_name

        if channel == "sms":
            sender_id = sender_id or sender_contact
            conversation_id = conversation_id or sender_id

        if not text_value or not conversation_id or not sender_id:
            return None

        return IncomingMessage(
            update_id=self._next_update_id(),
            backend="android",
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=text_value,
            sender_name=sender_name,
            sender_contact=sender_contact,
            sent_at=sent_at,
            event_type=channel,
            is_outgoing=bool(item.get("is_outgoing", False)),
        )

    def _next_update_id(self) -> int:
        self._update_counter += 1
        return self._update_counter

    @staticmethod
    def _compose_notification_text(*, title: str | None, body: str | None, package_name: str | None) -> str | None:
        parts = ["[Notification]"]
        if title:
            parts.append(title)
        if body:
            parts.append(body)
        elif package_name:
            parts.append(f"package={package_name}")
        lines = [part.strip() for part in parts if part and part.strip()]
        return "\n".join(lines) if len(lines) > 1 else None

    @staticmethod
    def _first_text(*values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)):
                rendered = str(value).strip()
                if rendered:
                    return rendered
        return None


def _state_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_offset(path: Path) -> int:
    payload = _state_payload(path)
    offset = payload.get("offset")
    return int(offset) if isinstance(offset, int) and offset >= 0 else 0


def _save_offset(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": max(0, offset)}) + "\n", encoding="utf-8")


def _jsonl_messages(inbox_path: Path, *, start_offset: int) -> tuple[list[dict[str, Any]], int]:
    if not inbox_path.exists():
        return [], 0
    file_size = inbox_path.stat().st_size
    offset = start_offset if 0 <= start_offset <= file_size else 0
    messages: list[dict[str, Any]] = []
    with inbox_path.open("r", encoding="utf-8") as handle:
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


def receive_events(*, inbox_path: Path, state_path: Path) -> dict[str, list[dict[str, Any]]]:
    offset = _load_offset(state_path)
    messages, new_offset = _jsonl_messages(inbox_path, start_offset=offset)
    _save_offset(state_path, new_offset)
    return {"messages": messages}


def send_sms(*, recipient: str, message: str, sms_command: str) -> None:
    if not which(sms_command):
        raise AndroidFrontendUnavailableError(
            f"Android SMS send failed: executable {sms_command!r} was not found in PATH"
        )
    proc = subprocess.run([sms_command, "-n", recipient, message], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or proc.stdout.strip() or "no stderr"
        raise RuntimeError(f"Android SMS send failed: {stderr}")


def queue_sms(*, recipient: str, message: str, outbox_path: Path) -> None:
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recipient": recipient,
        "message": message,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    with outbox_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


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
        raise RuntimeError(
            f"Refusing to overwrite existing SSH key: {private_key_path}. Pass --force to replace it."
        )
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    if which("ssh-keygen") is None:
        raise AndroidFrontendUnavailableError("Android SSH key generation failed: executable 'ssh-keygen' was not found in PATH")
    proc = subprocess.run(
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
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or proc.stdout.strip() or "no stderr"
        raise RuntimeError(f"Android SSH key generation failed: {stderr}")
    public_key_path = private_key_path.with_name(private_key_path.name + ".pub")
    return public_key_path.read_text(encoding="utf-8").strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="android-bridge", description="Android JSONL bridge for BUDUCCA")
    subparsers = parser.add_subparsers(dest="command", required=True)

    receive = subparsers.add_parser("receive", help="Read new Android events from a JSONL inbox")
    receive.add_argument("--inbox", default=DEFAULT_ANDROID_INBOX)
    receive.add_argument("--state-file", default=DEFAULT_ANDROID_STATE_FILE)

    send = subparsers.add_parser("send", help="Send an SMS using Termux:API")
    send.add_argument("--recipient", required=True)
    send.add_argument("--message", required=True)
    send.add_argument("--sms-command", default="termux-sms-send")
    send.add_argument("--outbox", default="")

    flush_outbox = subparsers.add_parser("flush-outbox", help="Send queued SMS requests from a JSONL outbox")
    flush_outbox.add_argument("--outbox", default=DEFAULT_ANDROID_OUTBOX)
    flush_outbox.add_argument("--state-file", default=DEFAULT_ANDROID_OUTBOX_STATE_FILE)
    flush_outbox.add_argument("--sms-command", default="termux-sms-send")

    generate_key = subparsers.add_parser("generate-ssh-key", help="Generate an SSH key for Android sync")
    generate_key.add_argument("--private-key", default=DEFAULT_ANDROID_SSH_KEY)
    generate_key.add_argument("--comment", default="buducca-android-sync")
    generate_key.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "receive":
            payload = receive_events(inbox_path=Path(args.inbox), state_path=Path(args.state_file))
            print(json.dumps(payload, ensure_ascii=False))
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
        if args.command == "generate-ssh-key":
            print(
                generate_ssh_key(
                    private_key_path=Path(args.private_key),
                    comment=args.comment,
                    force=bool(args.force),
                )
            )
            return 0
    except AndroidFrontendUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
