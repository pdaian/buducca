from __future__ import annotations

import json
import logging
import subprocess
from shutil import which
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


class SignalFrontendUnavailableError(RuntimeError):
    """Raised when Signal frontend is not runnable in current environment."""


class SignalClient:
    _VOICE_FILE_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".weba"}

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

    def _is_receive_json_configured(self) -> bool:
        command = self.receive_command
        for idx, token in enumerate(command):
            if token == "-o" and idx + 1 < len(command) and command[idx + 1].lower() == "json":
                return True
            if token.lower().startswith("--output=") and token.split("=", 1)[1].lower() == "json":
                return True
        return False

    def _validate_receive_command(self) -> None:
        if not self.receive_command:
            raise SignalFrontendUnavailableError("Signal frontend disabled: receive command is empty")

        executable = self.receive_command[0]
        if "/" not in executable and which(executable) is None:
            raise SignalFrontendUnavailableError(
                f"Signal frontend disabled: executable {executable!r} was not found in PATH"
            )

        if not self._is_receive_json_configured():
            raise SignalFrontendUnavailableError(
                "Signal frontend disabled: receive command must enable JSON output (for example `-o json`)"
            )

    def get_updates(self) -> list[IncomingMessage]:
        self._validate_receive_command()
        try:
            proc = subprocess.run(self.receive_command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise SignalFrontendUnavailableError(
                f"Signal frontend disabled: executable {exc.filename!r} was not found"
            ) from exc
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "no stderr"
            if self._is_registration_error(stderr):
                raise SignalFrontendUnavailableError(
                    "Signal frontend disabled: Signal account is not registered. "
                    "Set up signal-cli registration/linking first (phone number or QR). You can run `python3 -m telegram_llm_bot.signal_signup --config config.json` for docs. "
                    "(see README.md: 'Additional collector/signup commands')."
                )
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
            conversation_id, sender, text, voice_file_path = self._extract_message_fields(envelope)
            if not conversation_id or not sender:
                continue
            if not text and not voice_file_path:
                continue

            self._update_counter += 1
            messages.append(
                IncomingMessage(
                    update_id=self._update_counter,
                    backend="signal",
                    conversation_id=conversation_id,
                    sender_id=sender,
                    text=text,
                    voice_file_path=voice_file_path,
                )
            )
        return messages

    def _extract_message_fields(self, envelope: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
        sender = str(envelope.get("source") or "").strip()
        data_message = envelope.get("dataMessage") or {}
        if isinstance(data_message, dict):
            text = data_message.get("message")
            voice_file_path = self._find_voice_attachment_path(data_message)
            if sender and (text or voice_file_path):
                return sender, sender, text, voice_file_path

        sync_message = envelope.get("syncMessage") or {}
        if not isinstance(sync_message, dict):
            return "", "", None, None
        sent_message = sync_message.get("sentMessage") or {}
        if not isinstance(sent_message, dict):
            return "", "", None, None

        destination = str(sent_message.get("destination") or "").strip() or self.account
        source = sender or self.account
        text = sent_message.get("message")
        voice_file_path = self._find_voice_attachment_path(sent_message)
        return destination, source, text, voice_file_path

    def _is_registration_error(self, stderr: str) -> bool:
        normalized = stderr.lower()
        return "not registered" in normalized

    def _find_voice_attachment_path(self, data_message: dict[str, Any]) -> str | None:
        attachments = data_message.get("attachments") or []
        if not isinstance(attachments, list):
            return None
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if not self._is_voice_attachment(attachment):
                continue
            for field in ("storedFilename", "filename"):
                candidate = attachment.get(field)
                if isinstance(candidate, str) and candidate.strip():
                    path = Path(candidate.strip())
                    return str(path if path.is_absolute() else Path.cwd() / path)
        return None

    def _is_voice_attachment(self, attachment: dict[str, Any]) -> bool:
        content_type = str(attachment.get("contentType") or "").lower()
        if content_type.startswith("audio/"):
            return True

        voice_note_flag = attachment.get("voiceNote")
        if voice_note_flag is True:
            return True

        for field in ("storedFilename", "filename"):
            candidate = attachment.get(field)
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            if Path(candidate.strip()).suffix.lower() in self._VOICE_FILE_EXTENSIONS:
                return True
        return False

    def send_message(self, recipient: str, text: str) -> None:
        command = [part.replace("{recipient}", recipient).replace("{message}", text) for part in self.send_command]
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "no stderr"
            raise RuntimeError(f"Signal send command failed: {stderr}")
