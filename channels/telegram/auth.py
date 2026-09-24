"""Telegram sender authentication (plan §17D).

Exact chat_id whitelist; unknown senders are ignored (no reply) and written
to the audit log. Only private chats are accepted, so a whitelisted user who
adds the bot to a group does not give every group member control.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.log import get_logger

_audit = get_logger("audit")


class ChatWhitelist:
    def __init__(self, chat_ids: Iterable[str]) -> None:
        self._ids = frozenset(c.strip() for c in chat_ids if c.strip())
        for c in self._ids:
            if not c.lstrip("-").isdigit():
                raise ValueError(f"invalid Telegram chat_id in whitelist: {c!r}")

    @classmethod
    def from_csv(cls, value: str) -> ChatWhitelist:
        return cls(value.split(","))

    @property
    def chat_ids(self) -> frozenset[str]:
        return self._ids

    def __len__(self) -> int:
        return len(self._ids)

    def is_allowed(self, message: dict[str, Any]) -> bool:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = str(chat.get("id", ""))
        user_id = str(sender.get("id", ""))
        ok = (
            chat.get("type") == "private"
            and chat_id in self._ids
            and user_id == chat_id
        )
        if not ok:
            _audit.warning(
                "unauthorized telegram sender ignored",
                extra={
                    "action": "auth.reject", "status": "ignored",
                    "error_code": f"chat={chat_id} type={chat.get('type')} user={user_id}",
                },
            )
        return ok
