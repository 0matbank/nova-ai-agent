"""Channel-neutral message types. Telegram/WhatsApp adapters translate to and
from these, so the core never sees channel-specific formats."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class IncomingMessage:
    channel: str            # "telegram" | "whatsapp" | "local"
    chat_id: str
    user_id: str
    message_id: str
    text: str
    received_at: datetime

    @property
    def is_command(self) -> bool:
        return self.text.startswith("/")


@dataclass(frozen=True)
class OutgoingMessage:
    text: str
    # Inline buttons as rows of (label, callback_data); used from Phase 4.
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)


MessageHandler = Callable[[IncomingMessage], Awaitable[OutgoingMessage | None]]
