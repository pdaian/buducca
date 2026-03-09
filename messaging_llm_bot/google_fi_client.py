from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from shutil import which
from typing import Any


@dataclass
class IncomingMessage:
    update_id: int
    backend: str
    conversation_id: str
    sender_id: str
    text: str | None = None
    sender_name: str | None = None
    sender_contact: str | None = None
    event_type: str = "message"


class GoogleFiFrontendUnavailableError(RuntimeError):
    """Raised when Google Fi frontend is not runnable in current environment."""


class GoogleFiClient:
    def __init__(
        self,
        receive_command: list[str],
        send_command: list[str],
    ) -> None:
        self.receive_command = receive_command
        self.send_command = send_command
        self._update_counter = 0

    def _validate_receive_command(self) -> None:
        if not self.receive_command:
            raise GoogleFiFrontendUnavailableError("Google Fi frontend disabled: receive command is empty")
        executable = self.receive_command[0]
        if "/" not in executable and which(executable) is None:
            raise GoogleFiFrontendUnavailableError(
                f"Google Fi frontend disabled: executable {executable!r} was not found in PATH"
            )

    def _validate_send_command(self) -> None:
        if not self.send_command:
            raise GoogleFiFrontendUnavailableError("Google Fi frontend disabled: send command is empty")
        executable = self.send_command[0]
        if "/" not in executable and which(executable) is None:
            raise GoogleFiFrontendUnavailableError(
                f"Google Fi frontend disabled: executable {executable!r} was not found in PATH"
            )

    def get_updates(self) -> list[IncomingMessage]:
        self._validate_receive_command()
        try:
            proc = subprocess.run(self.receive_command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise GoogleFiFrontendUnavailableError(
                f"Google Fi frontend disabled: executable {exc.filename!r} was not found"
            ) from exc

        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "no stderr"
            raise RuntimeError(f"Google Fi receive command failed: {stderr}")

        return self._parse_updates(proc.stdout)

    def _parse_updates(self, stdout: str) -> list[IncomingMessage]:
        text = stdout.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Google Fi receive command must output valid JSON") from exc

        if isinstance(payload, dict):
            raw_messages = payload.get("messages", [])
            raw_calls = payload.get("calls", [])
        elif isinstance(payload, list):
            raw_messages = payload
            raw_calls = []
        else:
            return []

        updates: list[IncomingMessage] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            text_value = self._first_text(item.get("text"), item.get("body"), item.get("message"))
            conversation_id = self._first_text(
                item.get("conversation_id"), item.get("thread_id"), item.get("chat_id"), item.get("chatId")
            )
            sender_id = self._first_text(item.get("sender_id"), item.get("from"), item.get("sender"), item.get("number"))
            if not text_value or not conversation_id or not sender_id:
                continue
            sender_name = self._first_text(item.get("sender_name"), item.get("name"), item.get("display_name"))
            sender_contact = self._first_text(item.get("sender_contact"), sender_name, sender_id)
            updates.append(
                IncomingMessage(
                    update_id=self._next_update_id(),
                    backend="google_fi",
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    text=text_value,
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                    event_type="message",
                )
            )

        for item in raw_calls:
            if not isinstance(item, dict):
                continue
            conversation_id = self._first_text(
                item.get("conversation_id"), item.get("thread_id"), item.get("chat_id"), item.get("chatId")
            )
            sender_id = self._first_text(item.get("sender_id"), item.get("from"), item.get("caller"), item.get("number"))
            if not conversation_id or not sender_id:
                continue
            sender_name = self._first_text(item.get("sender_name"), item.get("name"), item.get("display_name"))
            sender_contact = self._first_text(item.get("sender_contact"), sender_name, sender_id)
            call_state = self._first_text(item.get("status"), item.get("state"), item.get("call_state")) or "received"
            updates.append(
                IncomingMessage(
                    update_id=self._next_update_id(),
                    backend="google_fi",
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    text=f"[Call event] {call_state}",
                    sender_name=sender_name,
                    sender_contact=sender_contact,
                    event_type="call",
                )
            )

        return updates

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
        try:
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise GoogleFiFrontendUnavailableError(
                f"Google Fi frontend disabled: executable {exc.filename!r} was not found"
            ) from exc
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "no stderr"
            raise RuntimeError(f"Google Fi send command failed: {stderr}")
