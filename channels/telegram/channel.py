"""Telegram long-polling channel (plan §6): receive → authenticate → hand a
channel-neutral IncomingMessage to the core handler → send the reply."""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime
from typing import Any

from channels.base import (
    CallbackHandler,
    CallbackReply,
    IncomingCallback,
    IncomingMessage,
    MessageHandler,
    OutgoingMessage,
    VoiceRef,
)
from channels.telegram.api import (
    TelegramAPI,
    TelegramAuthError,
    TelegramError,
    TelegramRetryAfter,
    TelegramTransientError,
    chunk_text,
)
from channels.telegram.auth import ChatWhitelist
from core.log import get_logger

_log = get_logger("core")

MAX_BACKOFF_SECONDS = 60
UNSUPPORTED_REPLY = "এখন text আর voice message সমর্থিত। ছবি/file পরের phase-এ চালু হবে।"
ERROR_REPLY = "দুঃখিত, এই message প্রসেস করতে গিয়ে internal error হয়েছে। Log-এ বিস্তারিত আছে।"


class TelegramChannel:
    name = "telegram"

    def __init__(
        self,
        api: TelegramAPI,
        whitelist: ChatWhitelist,
        handler: MessageHandler,
        poll_timeout: int = 50,
        callback_handler: CallbackHandler | None = None,
    ) -> None:
        self.api = api
        self.whitelist = whitelist
        self.handler = handler
        self.callback_handler = callback_handler
        self.poll_timeout = poll_timeout
        self.offset: int | None = None
        self.last_poll_ok: float | None = None
        self.connected = False

    # ------------------------------------------------------------- polling

    async def _fetch(self) -> list[dict[str, Any]]:
        updates = await self.api.get_updates(self.offset, self.poll_timeout)
        self.last_poll_ok = time.time()
        if not self.connected:
            self.connected = True
            _log.info("telegram connected", extra={"action": "telegram.poll", "status": "ok"})
        return updates

    async def _process(self, updates: list[dict[str, Any]]) -> None:
        for update in updates:
            # Advance first: a crashing handler must not cause endless redelivery.
            self.offset = int(update["update_id"]) + 1
            await self._handle_update(update)

    async def poll_once(self) -> int:
        """One getUpdates round. Returns number of updates processed."""
        updates = await self._fetch()
        await self._process(updates)
        return len(updates)

    async def _fetch_or_stop(self, stop: asyncio.Event) -> list[dict[str, Any]] | None:
        """Long-poll, but return None as soon as `stop` is set. Only the network
        wait is cancelled — never the handling of an already received message."""
        fetch = asyncio.create_task(self._fetch())
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({fetch, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if fetch not in done:
            fetch.cancel()
            await asyncio.gather(fetch, return_exceptions=True)
            return None
        stopper.cancel()
        return fetch.result()

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                updates = await self._fetch_or_stop(stop)
                if updates is None:
                    break
                await self._process(updates)
                backoff = 1.0
            except TelegramRetryAfter as e:
                _log.warning(str(e), extra={"action": "telegram.poll", "status": "rate_limited"})
                await _sleep_or_stop(stop, e.retry_after)
            except TelegramAuthError as e:
                self.connected = False
                _log.critical(str(e), extra={"action": "telegram.poll", "status": "auth_failed",
                                             "error_code": "AUTH_REQUIRED"})
                raise
            except (TelegramTransientError, TelegramError) as e:
                self.connected = False
                _log.warning(f"{e}; retrying in {backoff:.0f}s",
                             extra={"action": "telegram.poll", "status": "retrying"})
                await _sleep_or_stop(stop, backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
        await self.acknowledge()

    async def acknowledge(self) -> None:
        """Confirm processed updates to Telegram so they are not redelivered."""
        if self.offset is None:
            return
        try:
            await self.api.get_updates(self.offset, 0)
        except TelegramError as e:
            _log.warning(f"final acknowledge failed: {e}", extra={"action": "telegram.ack"})

    # ------------------------------------------------------------ handling

    async def _handle_callback(self, cq: dict[str, Any]) -> None:
        message = cq.get("message") or {}
        # Authenticate the tap exactly like a message: private chat, whitelisted,
        # and the tapping user must be the chat owner.
        if not self.whitelist.is_allowed({"chat": message.get("chat") or {},
                                          "from": cq.get("from") or {}}):
            return
        if self.callback_handler is None:
            return
        chat_id = str(message["chat"]["id"])
        incoming = IncomingCallback(
            channel=self.name, chat_id=chat_id, user_id=str(cq["from"]["id"]),
            callback_id=str(cq["id"]), data=str(cq.get("data", "")),
            message_id=str(message.get("message_id", "")),
            message_text=str(message.get("text", "")),
        )
        _log.info("button tapped", extra={"action": "telegram.callback"})
        try:
            reply = await self.callback_handler(incoming)
        except Exception:
            _log.exception("callback handler failed", extra={"action": "telegram.callback",
                                                             "status": "error"})
            reply = CallbackReply("Internal error")
        try:
            await self.api.answer_callback_query(incoming.callback_id,
                                                 reply.toast if reply else "")
            if reply is not None and reply.new_text is not None and incoming.message_id:
                await self.api.edit_message_text(chat_id, incoming.message_id, reply.new_text)
        except TelegramError as e:
            _log.warning(f"callback answer failed: {e}", extra={"action": "telegram.callback"})

    async def _handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return
        message = update.get("message")
        if not message:
            return
        if not self.whitelist.is_allowed(message):
            return
        chat_id = str(message["chat"]["id"])
        text = message.get("text")
        voice = None
        media = message.get("voice") or message.get("audio") or message.get("video_note")
        if text is None and media and media.get("file_id"):
            voice = VoiceRef(file_id=str(media["file_id"]), duration=int(media.get("duration", 0)),
                             mime_type=str(media.get("mime_type", "audio/ogg")),
                             size=int(media.get("file_size", 0)))
            text = str(message.get("caption") or "")
        if text is None:
            await self.send(chat_id, OutgoingMessage(UNSUPPORTED_REPLY))
            return

        incoming = IncomingMessage(
            channel=self.name, chat_id=chat_id, user_id=str(message["from"]["id"]),
            message_id=str(message["message_id"]), text=text,
            received_at=datetime.fromtimestamp(int(message.get("date", time.time())), UTC),
            voice=voice,
        )
        command = ("<voice>" if voice else
                   text.split()[0] if incoming.is_command else "<text>")
        _log.info("message received", extra={"action": f"telegram.recv {command}"})
        try:
            reply = await self.handler(incoming)
        except Exception:
            _log.exception("handler failed", extra={"action": "telegram.handle",
                                                    "status": "error"})
            reply = OutgoingMessage(ERROR_REPLY)
        if reply is not None:
            await self.send(chat_id, reply)

    async def send(self, chat_id: str, message: OutgoingMessage) -> None:
        if message.photo is not None:
            caption, rest = message.text[:1024], message.text[1024:]
            try:
                await self.api.send_photo(chat_id, message.photo, caption)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                await self.api.send_photo(chat_id, message.photo, caption)
            except TelegramError as e:
                _log.error(f"photo send failed: {e}", extra={"action": "telegram.send_photo",
                                                             "status": "error"})
                rest = f"(ছবি পাঠানো যায়নি: {e})\n{message.text}"
            if not rest and not message.buttons:
                return
            message = OutgoingMessage(rest or "⬆️", message.buttons)
        markup = None
        if message.buttons:
            markup = {"inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in row]
                for row in message.buttons
            ]}
        parts = chunk_text(message.text)
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            await self._send_with_retry(chat_id, part, markup if last else None)

    async def _send_with_retry(
        self, chat_id: str, text: str, markup: dict[str, Any] | None
    ) -> None:
        for attempt in range(3):
            try:
                await self.api.send_message(chat_id, text, markup)
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramTransientError:
                await asyncio.sleep(2 ** attempt)
            except TelegramError as e:
                _log.error(f"send failed: {e}", extra={"action": "telegram.send",
                                                       "status": "error"})
                return
        _log.error("send failed after retries", extra={"action": "telegram.send",
                                                       "status": "error"})

    async def broadcast(self, message: OutgoingMessage) -> None:
        for chat_id in sorted(self.whitelist.chat_ids):
            await self.send(chat_id, message)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
