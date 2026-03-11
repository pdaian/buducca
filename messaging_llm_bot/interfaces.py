from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class IncomingAttachment:
    file_id: str | None = None
    file_path: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    content: bytes | None = None


@dataclass(slots=True)
class IncomingMessage:
    update_id: int
    backend: str = "telegram"
    conversation_id: str = ""
    conversation_name: str | None = None
    sender_id: str = ""
    chat_id: int | None = None
    text: str | None = None
    voice_file_id: str | None = None
    voice_file_path: str | None = None
    sender_name: str | None = None
    sender_contact: str | None = None
    sent_at: str | None = None
    event_type: str = "message"
    attachments: list[IncomingAttachment] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.chat_id is not None:
            if not self.conversation_id:
                self.conversation_id = str(self.chat_id)
            if not self.sender_id:
                self.sender_id = str(self.chat_id)
