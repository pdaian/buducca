from __future__ import annotations

import json
import logging
import subprocess
import time
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
    sender_name: str | None = None


class SignalFrontendUnavailableError(RuntimeError):
    """Raised when Signal frontend is not runnable in current environment."""


class SignalClient:
    GROUP_CONVERSATION_PREFIX = "group:"
    GROUP_ID_DELIMITER = "|"
    _VOICE_FILE_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".weba"}

    def __init__(
        self,
        account: str,
        receive_command: list[str] | None = None,
        send_command: list[str] | None = None,
        contacts_command: list[str] | None = None,
        groups_command: list[str] | None = None,
        contacts_cache_ttl_seconds: int = 300,
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
        self.contacts_command = contacts_command or ["signal-cli", "-o", "json", "-a", account, "listContacts"]
        self.groups_command = groups_command or ["signal-cli", "-o", "json", "-a", account, "listGroups"]
        self.contacts_cache_ttl_seconds = max(0, contacts_cache_ttl_seconds)
        self.group_send_command = ["signal-cli", "-a", account, "send", "-m", "{message}", "-g", "{group_id}"]
        self._update_counter = 0
        self._contact_names: dict[str, str] = {}
        self._group_names: dict[str, str] = {}
        self._contact_names_loaded_at = 0.0
        self._group_names_loaded_at = 0.0

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
        self._refresh_contact_cache_if_needed()
        self._refresh_group_cache_if_needed()
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
            sender_name = self._extract_sender_name(envelope, sender)

            self._update_counter += 1
            messages.append(
                IncomingMessage(
                    update_id=self._update_counter,
                    backend="signal",
                    conversation_id=conversation_id,
                    sender_id=sender,
                    text=text,
                    voice_file_path=voice_file_path,
                    sender_name=sender_name,
                )
            )
        return messages

    def _refresh_contact_cache_if_needed(self) -> None:
        if not self.contacts_command:
            return

        now = time.monotonic()
        if self._contact_names and now - self._contact_names_loaded_at < self.contacts_cache_ttl_seconds:
            return

        try:
            proc = subprocess.run(self.contacts_command, capture_output=True, text=True, check=False)
        except OSError:
            logging.debug("Unable to refresh signal contacts cache", exc_info=True)
            return

        if proc.returncode != 0:
            logging.debug("Signal contacts command failed: %s", proc.stderr.strip() or "no stderr")
            return

        parsed = self._parse_contacts_output(proc.stdout)
        if parsed:
            self._contact_names = parsed
            self._contact_names_loaded_at = now

    def _parse_contacts_output(self, output: str) -> dict[str, str]:
        contacts: dict[str, str] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            if isinstance(payload, dict):
                self._parse_contact_payload(payload, contacts)
            elif isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        self._parse_contact_payload(item, contacts)
        return contacts

    def _parse_contact_payload(self, payload: dict[str, Any], contacts: dict[str, str]) -> None:
        number = self._first_non_empty_string(payload.get("number"), payload.get("recipient"), payload.get("uuid"))
        if not number:
            return
        name = self._first_non_empty_string(payload.get("name"), payload.get("profileName"), payload.get("givenName"))
        if name:
            contacts[number] = name

    def _extract_sender_name(self, envelope: dict[str, Any], sender: str) -> str | None:
        profile = envelope.get("sourceProfile")
        profile_name = profile.get("name") if isinstance(profile, dict) else None
        sender_name = self._first_non_empty_string(envelope.get("sourceName"), profile_name)
        if sender_name:
            self._contact_names[sender] = sender_name
            return sender_name
        return self._contact_names.get(sender)

    def _extract_message_fields(self, envelope: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
        sender = self._first_non_empty_string(
            envelope.get("sourceNumber"),
            envelope.get("source"),
            envelope.get("sourceUuid"),
        )
        data_message = envelope.get("dataMessage") or {}
        if isinstance(data_message, dict):
            group_id = self._extract_group_id(data_message)
            conversation_id = (
                self._build_group_conversation_id(data_message)
                if group_id
                else sender
            )
            text = data_message.get("message")
            voice_file_path = self._find_voice_attachment_path(data_message)
            if sender and conversation_id and (text or voice_file_path):
                return conversation_id, sender, text, voice_file_path

        sync_message = envelope.get("syncMessage") or {}
        if not isinstance(sync_message, dict):
            return "", "", None, None
        sent_message = sync_message.get("sentMessage") or {}
        if not isinstance(sent_message, dict):
            return "", "", None, None

        group_id = self._extract_group_id(sent_message)
        if group_id:
            destination = self._build_group_conversation_id(sent_message)
        else:
            destination = (
                self._first_non_empty_string(
                    sent_message.get("destinationNumber"),
                    sent_message.get("destination"),
                    sent_message.get("destinationUuid"),
                )
                or self.account
            )
        source = sender or self.account
        text = sent_message.get("message")
        voice_file_path = self._find_voice_attachment_path(sent_message)
        return destination, source, text, voice_file_path

    def _extract_group_id(self, message: dict[str, Any]) -> str:
        group_info = message.get("groupInfo")
        if not isinstance(group_info, dict):
            return ""
        return self._first_non_empty_string(group_info.get("groupId"), group_info.get("groupID"), group_info.get("id"))

    def _extract_group_title(self, message: dict[str, Any]) -> str:
        group_info = message.get("groupInfo")
        if not isinstance(group_info, dict):
            return ""
        group_title = self._first_non_empty_string(group_info.get("title"), group_info.get("name"))
        group_id = self._extract_group_id(message)
        if group_id and group_title:
            self._group_names[group_id] = group_title
        return group_title

    def _build_group_conversation_id(self, message: dict[str, Any]) -> str:
        group_id = self._extract_group_id(message)
        if not group_id:
            return ""

        title = self._extract_group_title(message) or self._group_names.get(group_id, "")
        if title:
            return f"{self.GROUP_CONVERSATION_PREFIX}{title}{self.GROUP_ID_DELIMITER}{group_id}"
        return f"{self.GROUP_CONVERSATION_PREFIX}{group_id}"

    def _refresh_group_cache_if_needed(self) -> None:
        if not self.groups_command:
            return

        now = time.monotonic()
        if self._group_names and now - self._group_names_loaded_at < self.contacts_cache_ttl_seconds:
            return

        try:
            proc = subprocess.run(self.groups_command, capture_output=True, text=True, check=False)
        except OSError:
            logging.debug("Unable to refresh signal groups cache", exc_info=True)
            return

        if proc.returncode != 0:
            logging.debug("Signal groups command failed: %s", proc.stderr.strip() or "no stderr")
            return

        parsed = self._parse_groups_output(proc.stdout)
        if parsed:
            self._group_names = parsed
            self._group_names_loaded_at = now

    def _parse_groups_output(self, output: str) -> dict[str, str]:
        groups: dict[str, str] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            if isinstance(payload, dict):
                self._parse_group_payload(payload, groups)
            elif isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        self._parse_group_payload(item, groups)
        return groups

    def _parse_group_payload(self, payload: dict[str, Any], groups: dict[str, str]) -> None:
        group_id = self._first_non_empty_string(payload.get("id"), payload.get("groupId"), payload.get("groupID"))
        if not group_id:
            return
        group_name = self._first_non_empty_string(payload.get("name"), payload.get("title"), payload.get("description"))
        if group_name:
            groups[group_id] = group_name

    def _extract_group_id_from_recipient(self, recipient: str) -> str:
        if not recipient.startswith(self.GROUP_CONVERSATION_PREFIX):
            return ""

        payload = recipient[len(self.GROUP_CONVERSATION_PREFIX) :].strip()
        if not payload:
            return ""

        if self.GROUP_ID_DELIMITER in payload:
            return payload.rsplit(self.GROUP_ID_DELIMITER, 1)[1].strip()
        return payload

    def _first_non_empty_string(self, *candidates: Any) -> str:
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            value = candidate.strip()
            if value:
                return value
        return ""

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
        group_id = self._extract_group_id_from_recipient(recipient)

        if group_id:
            template = self.send_command if any("{group_id}" in part for part in self.send_command) else self.group_send_command
        else:
            template = self.send_command

        command = [
            part.replace("{recipient}", recipient).replace("{message}", text).replace("{group_id}", group_id)
            for part in template
        ]
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or "no stderr"
            raise RuntimeError(f"Signal send command failed: {stderr}")
