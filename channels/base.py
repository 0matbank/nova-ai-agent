"""Channel-neutral message types. Telegram/WhatsApp adapters translate to and
from these, so the core never sees channel-specific formats."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class VoiceRef:
    """A voice/audio attachment; the channel knows how to download `file_id`."""
    file_id: str
    duration: int
    mime_type: str
    size: int


@dataclass(frozen=True)
class IncomingMessage:
    channel: str            # "telegram" | "whatsapp" | "local"
    chat_id: str
    user_id: str
    message_id: str
    text: str
    received_at: datetime
    voice: VoiceRef | None = None

    @property
    def is_command(self) -> bool:
        return self.text.startswith("/")


@dataclass(frozen=True)
class OutgoingMessage:
    text: str
    # Inline buttons as rows of (label, callback_data); used from Phase 4.
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)
    # Optional JPEG image (e.g. /screenshot); `text` becomes its caption.
    photo: bytes | None = None


@dataclass(frozen=True)
class IncomingCallback:
    """A button tap (Telegram callback_query)."""
    channel: str
    chat_id: str
    user_id: str
    callback_id: str
    data: str
    message_id: str
    message_text: str


@dataclass(frozen=True)
class CallbackReply:
    toast: str                       # short popup shown to the user
    new_text: str | None = None      # replaces the message text and removes its buttons


MessageHandler = Callable[[IncomingMessage], Awaitable[OutgoingMessage | None]]
CallbackHandler = Callable[[IncomingCallback], Awaitable[CallbackReply | None]]
