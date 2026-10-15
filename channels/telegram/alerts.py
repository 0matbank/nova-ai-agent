"""Forward CRITICAL log records to Telegram (plan §17E: "Critical error
Telegram-এ alert করবে"). The text goes through the secret redactor here: this
handler is attached without the JSON formatter, so nothing upstream redacts it
(audit 2026-10-15)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging

from channels.base import OutgoingMessage
from channels.telegram.channel import TelegramChannel
from core.log import redact


class TelegramAlertHandler(logging.Handler):
    def __init__(self, channel: TelegramChannel, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(level=logging.CRITICAL)
        self.channel = channel
        self.loop = loop
        self._pending: set[concurrent.futures.Future[None]] = set()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            rendered = self.format(record)
            try:
                message = json.loads(rendered).get("message", rendered)
            except ValueError:
                message = rendered
            text = f"🚨 CRITICAL ALERT\n{redact(str(message))}"
            fut = asyncio.run_coroutine_threadsafe(
                self.channel.broadcast(OutgoingMessage(text)), self.loop
            )
            self._pending.add(fut)
            fut.add_done_callback(self._pending.discard)
        except Exception:
            self.handleError(record)
