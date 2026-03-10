from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from shutil import which
from typing import Any

from .interfaces import IncomingMessage


class WhatsAppFrontendUnavailableError(RuntimeError):
    """Raised when WhatsApp frontend is not runnable in current environment."""


class WhatsAppClient:
    GROUP_CONVERSATION_PREFIX = "group:"
    GROUP_ID_DELIMITER = "|"
    LEGACY_REPO_ROOT = Path("/home/ai/buducca")

    def __init__(
        self,
        receive_command: list[str],
        send_command: list[str],
    ) -> None:
        self.receive_command = receive_command
        self.send_command = send_command
        self._update_counter = 0
        self._repo_root = Path(__file__).resolve().parent.parent

    def _normalize_command_paths(self, command: list[str]) -> list[str]:
        if not command:
            return command
        normalized: list[str] = []
        for part in command:
            candidate = Path(part)
            if candidate.is_absolute() and str(candidate).startswith(str(self.LEGACY_REPO_ROOT)) and not candidate.exists():
                try:
                    suffix = candidate.relative_to(self.LEGACY_REPO_ROOT)
                except ValueError:
                    normalized.append(part)
                    continue
                replacement = self._repo_root / suffix
                normalized.append(str(replacement) if replacement.exists() else part)
                continue

            if not candidate.is_absolute() and "/" in part and not candidate.exists():
                replacement = self._repo_root / candidate
                normalized.append(str(replacement) if replacement.exists() else part)
                continue

            normalized.append(part)
        return normalized

    def _validate_receive_command(self) -> None:
        if not self.receive_command:
            raise WhatsAppFrontendUnavailableError("WhatsApp frontend disabled: receive command is empty")
        executable = self.receive_command[0]
        if "/" not in executable and which(executable) is None:
            raise WhatsAppFrontendUnavailableError(
                f"WhatsApp frontend disabled: executable {executable!r} was not found in PATH"
            )

    def _validate_send_command(self) -> None:
        if not self.send_command:
            raise WhatsAppFrontendUnavailableError("WhatsApp frontend disabled: send command is empty")
        executable = self.send_command[0]
        if "/" not in executable and which(executable) is None:
            raise WhatsAppFrontendUnavailableError(
                f"WhatsApp frontend disabled: executable {executable!r} was not found in PATH"
            )

    @staticmethod
    def _missing_python_script(command: list[str]) -> str | None:
        if len(command) < 2:
            return None
        executable = Path(command[0]).name.lower()
        if "python" not in executable:
            return None
        for part in command[1:]:
            if part.startswith("-"):
                continue
            script_candidate = Path(part)
            if script_candidate.suffix != ".py":
                continue
            if not script_candidate.is_absolute():
                continue
            if not script_candidate.exists():
                return part
            break
        return None

    def get_updates(self) -> list[IncomingMessage]:
        self._validate_receive_command()
        command = self._normalize_command_paths(self.receive_command)
        missing_script = self._missing_python_script(command)
        if missing_script:
            raise WhatsAppFrontendUnavailableError(
                f"WhatsApp frontend disabled: script {missing_script!r} does not exist"
            )
        try:
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise WhatsAppFrontendUnavailableError(
                f"WhatsApp frontend disabled: executable {exc.filename!r} was not found"
            ) from exc

        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "no stderr"
            raise RuntimeError(f"WhatsApp receive command failed: {stderr}")

        return self._parse_updates(proc.stdout)

    def _parse_updates(self, stdout: str) -> list[IncomingMessage]:
        text = stdout.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("WhatsApp receive command must output valid JSON") from exc

        if isinstance(payload, dict):
            raw_messages = payload.get("messages", [])
        elif isinstance(payload, list):
            raw_messages = payload
        else:
            return []

        messages: list[IncomingMessage] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            text_value = self._first_text(item.get("text"), item.get("body"), item.get("message"))
            if not text_value:
                continue
            conversation_id = self._first_text(
                item.get("conversation_id"), item.get("chat_id"), item.get("chatId"), item.get("room_id")
            )
            sender_id = self._first_text(item.get("sender_id"), item.get("from"), item.get("author"), item.get("sender"))
            if not conversation_id or not sender_id:
                continue
            sender_name = self._first_text(item.get("sender_name"), item.get("name"), item.get("pushName"))
            sender_contact = self._first_text(item.get("sender_contact"), item.get("contact"), sender_name, sender_id)
            messages.append(
                IncomingMessage(
                    update_id=self._next_update_id(),
                    backend="whatsapp",
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    text=text_value,
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                )
            )
        return messages

    def _next_update_id(self) -> int:
        self._update_counter += 1
        return self._update_counter

    @staticmethod
    def _first_text(*values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def send_message(self, recipient: str, text: str) -> None:
        self._validate_send_command()
        command = [part.replace("{recipient}", recipient).replace("{message}", text) for part in self.send_command]
        command = self._normalize_command_paths(command)
        missing_script = self._missing_python_script(command)
        if missing_script:
            raise WhatsAppFrontendUnavailableError(
                f"WhatsApp frontend disabled: script {missing_script!r} does not exist"
            )
        try:
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise WhatsAppFrontendUnavailableError(
                f"WhatsApp frontend disabled: executable {exc.filename!r} was not found"
            ) from exc
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "no stderr"
            raise RuntimeError(f"WhatsApp send command failed: {stderr}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whatsapp-client", description="Built-in WhatsApp JSON frontend stubs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    receive = subparsers.add_parser("receive", help="emit JSON updates")
    receive.add_argument("--account", default="default")

    send = subparsers.add_parser("send", help="send a message (stub)")
    send.add_argument("--account", default="default")
    send.add_argument("--recipient", required=True)
    send.add_argument("--message", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "receive":
        print(json.dumps({"messages": []}))
        return 0

    if args.command == "send":
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
