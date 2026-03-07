from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class IncomingMessage:
    update_id: int
    backend: str
    conversation_id: str
    sender_id: str
    text: str | None = None
    voice_file_path: str | None = None


class SignalClient:
    def __init__(
        self,
        account: str,
        receive_command: list[str] | None = None,
        send_command: list[str] | None = None,
        poll_timeout_seconds: int = 1,
    ) -> None:
        self.account = account
        self.receive_command = receive_command or [
            "signal-cli",
            "-o",
            "json",
            "-a",
            account,
            "receive",
            "--timeout",
            str(poll_timeout_seconds),
        ]
        self.send_command = send_command or ["signal-cli", "-a", account, "send", "-m", "{message}", "{recipient}"]
        self._update_counter = 0

    def get_updates(self) -> list[IncomingMessage]:
        proc = subprocess.run(self.receive_command, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "no stderr"
            raise RuntimeError(f"Signal receive command failed: {stderr}")

        messages: list[IncomingMessage] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            try:
                payload: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                logging.debug("Skipping non-JSON signal-cli output line: %s", line)
                continue

            envelope = payload.get("envelope") or {}
            data_message = envelope.get("dataMessage") or {}
            sender = str(envelope.get("source") or "").strip()
            if not sender:
                continue

            text = data_message.get("message")
            voice_file_path = self._find_voice_attachment_path(data_message)
            if not text and not voice_file_path:
                continue

            self._update_counter += 1
            messages.append(
                IncomingMessage(
                    update_id=self._update_counter,
                    backend="signal",
                    conversation_id=sender,
                    sender_id=sender,
                    text=text,
                    voice_file_path=voice_file_path,
                )
            )
        return messages

    def _find_voice_attachment_path(self, data_message: dict[str, Any]) -> str | None:
        attachments = data_message.get("attachments") or []
        if not isinstance(attachments, list):
            return None
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            content_type = str(attachment.get("contentType") or "").lower()
            if not content_type.startswith("audio/"):
                continue
            for field in ("storedFilename", "filename"):
                candidate = attachment.get(field)
                if isinstance(candidate, str) and candidate.strip():
                    path = Path(candidate.strip())
                    return str(path if path.is_absolute() else Path.cwd() / path)
        return None

    def send_message(self, recipient: str, text: str) -> None:
        command = [part.replace("{recipient}", recipient).replace("{message}", text) for part in self.send_command]
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "no stderr"
            raise RuntimeError(f"Signal send command failed: {stderr}")
