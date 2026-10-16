"""WhatsApp channel (plan §6 Secondary, Phase 21) on the official Cloud API.

Meta → (Cloudflare tunnel) → local webhook → signature check (X-Hub-Signature-256,
HMAC-SHA256 with the App Secret) → owner-only → the SAME core handler as Telegram
(tasks, approvals, verifier, memory). Replies go back through the Graph API.

- Owner-only: the sender must be one of WHATSAPP_ALLOWED_NUMBERS and the event must
  be for Nova's own phone_number_id; anything else is dropped and audited.
- Approvals: interactive reply buttons → the same callback path as Telegram's.
- Administrative commands stay on Telegram (plan §6): /lockdown etc. are refused.
- 24-hour rule: free-form messages are only allowed within 24 h of the owner's last
  WhatsApp message; outside it the message goes to Telegram instead (fallback).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from channels.base import (
    CallbackHandler,
    CallbackReply,
    IncomingCallback,
    IncomingMessage,
    MessageHandler,
    OutgoingMessage,
    VoiceRef,
)
from channels.whatsapp.api import WhatsAppAPI, WhatsAppError, WhatsAppWindowClosed
from core.log import get_logger

_log = get_logger("core")
_audit = get_logger("audit")

PREFIX = "wa:"
WINDOW_SECONDS = 24 * 3600 - 15 * 60          # stop 15 min early, to be safe
TELEGRAM_ONLY = ("/lockdown", "/restart-agent", "/update", "/skills")
UNSUPPORTED = "WhatsApp-এ এখন text, voice আর button সমর্থিত।"
ADMIN_ONLY = "🔐 এই command শুধু Telegram-এ চলে (administrative কাজ Telegram-এ)।"
ERROR_REPLY = "দুঃখিত, এই message প্রসেস করতে গিয়ে internal error হয়েছে। Log-এ বিস্তারিত আছে।"

Fallback = Callable[[str, OutgoingMessage], Awaitable[None]]


def digits(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


def valid_signature(app_secret: SecretStr, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    want = hmac.new(app_secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header.split("=", 1)[1].strip())


class WhatsAppChannel:
    name = "whatsapp"

    def __init__(self, api: WhatsAppAPI, allowed_numbers: list[str], handler: MessageHandler,
                 callback_handler: CallbackHandler | None = None,
                 fallback: Fallback | None = None) -> None:
        self.api = api
        self.allowed = {digits(n) for n in allowed_numbers if digits(n)}
        self.handler = handler
        self.callback_handler = callback_handler
        self.fallback = fallback                # Telegram, outside the 24 h window
        self.last_inbound: dict[str, float] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()
        self.connected = False
        self.public_url: str | None = None

    # ----------------------------------------------------------- receiving

    def _new(self, message_id: str) -> bool:
        """Meta may deliver the same event more than once."""
        if message_id in self._seen:
            return False
        self._seen[message_id] = None
        while len(self._seen) > 2000:
            self._seen.popitem(last=False)
        return True

    async def on_webhook(self, payload: dict[str, Any]) -> int:
        """One verified webhook POST; returns how many owner messages were handled."""
        handled = 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                meta = value.get("metadata") or {}
                if str(meta.get("phone_number_id", "")) != self.api.phone_number_id:
                    _audit.warning("whatsapp event for another phone number ignored",
                                   extra={"action": "whatsapp.reject", "status": "denied"})
                    continue
                for status in value.get("statuses") or []:
                    if status.get("status") == "failed":
                        _log.warning(f"whatsapp delivery failed: {status.get('errors')}",
                                     extra={"action": "whatsapp.status", "status": "failed"})
                for msg in value.get("messages") or []:
                    if await self._message(msg):
                        handled += 1
        return handled

    async def _message(self, msg: dict[str, Any]) -> bool:
        sender = digits(str(msg.get("from", "")))
        if sender not in self.allowed:
            _audit.warning(f"whatsapp message from unknown number …{sender[-4:]} ignored",
                           extra={"action": "whatsapp.reject", "status": "denied"})
            return False
        mid = str(msg.get("id", ""))
        if not mid or not self._new(mid):
            return False
        self.connected = True
        self.last_inbound[sender] = time.time()
        chat_id = PREFIX + sender
        received = datetime.fromtimestamp(int(msg.get("timestamp") or time.time()), UTC)
        kind = msg.get("type")
        try:
            await self.api.mark_read(mid)
        except WhatsAppError as e:
            _log.debug(f"mark read failed: {e}", extra={"action": "whatsapp.read"})
        if kind == "interactive":
            reply = (msg.get("interactive") or {}).get("button_reply") or {}
            await self._callback(chat_id, sender, mid, str(reply.get("id", "")))
            return True
        text: str | None = None
        voice = None
        if kind == "text":
            text = str((msg.get("text") or {}).get("body", ""))
        elif kind == "audio":
            audio = msg.get("audio") or {}
            voice = VoiceRef(file_id=str(audio.get("id", "")), duration=0,
                             mime_type=str(audio.get("mime_type", "audio/ogg")), size=0)
            text = ""
        if text is None:
            await self.send(chat_id, OutgoingMessage(UNSUPPORTED))
            return True
        if text.strip().split(" ")[0].lower().split("@")[0] in TELEGRAM_ONLY:
            await self.send(chat_id, OutgoingMessage(ADMIN_ONLY))
            return True
        incoming = IncomingMessage(channel=self.name, chat_id=chat_id, user_id=sender,
                                   message_id=mid, text=text, received_at=received,
                                   voice=voice)
        _log.info("message received", extra={"action": "whatsapp.recv " + (
            "<voice>" if voice else text.split()[0] if incoming.is_command else "<text>")})
        try:
            out = await self.handler(incoming)
        except Exception:
            _log.exception("handler failed", extra={"action": "whatsapp.handle",
                                                    "status": "error"})
            out = OutgoingMessage(ERROR_REPLY)
        if out is not None:
            await self.send(chat_id, out)
        return True

    async def _callback(self, chat_id: str, sender: str, mid: str, data: str) -> None:
        if self.callback_handler is None or not data:
            return
        incoming = IncomingCallback(channel=self.name, chat_id=chat_id, user_id=sender,
                                    callback_id=mid, data=data, message_id="",
                                    message_text="")
        _log.info("button tapped", extra={"action": "whatsapp.callback"})
        try:
            reply: CallbackReply | None = await self.callback_handler(incoming)
        except Exception:
            _log.exception("callback handler failed", extra={"action": "whatsapp.callback",
                                                             "status": "error"})
            reply = CallbackReply("Internal error")
        if reply is not None and (reply.new_text or reply.toast):
            await self.send(chat_id, OutgoingMessage(reply.new_text or reply.toast))

    async def download(self, media_id: str) -> bytes:
        return await self.api.download_media(media_id)

    # -------------------------------------------------------------- sending

    def window_open(self, number: str) -> bool:
        last = self.last_inbound.get(digits(number))
        return last is not None and time.time() - last < WINDOW_SECONDS

    async def send(self, chat_id: str, message: OutgoingMessage) -> None:
        to = digits(chat_id.removeprefix(PREFIX))
        if not self.window_open(to):
            await self._fallback(chat_id, message, "the 24 h WhatsApp window is closed")
            return
        try:
            await self._send(to, message)
        except WhatsAppWindowClosed as e:
            self.last_inbound.pop(to, None)
            await self._fallback(chat_id, message, str(e))
        except WhatsAppError as e:
            _log.error(f"whatsapp send failed: {e}", extra={"action": "whatsapp.send",
                                                            "status": "error"})
            await self._fallback(chat_id, message, str(e))

    async def _send(self, to: str, m: OutgoingMessage) -> None:
        text = m.text
        if m.voice is not None:
            await self.api.send_media(to, "audio", m.voice, "audio/ogg", "reply.ogg")
        if m.video is not None:
            await self.api.send_media(to, "video", m.video[1], "video/mp4", m.video[0],
                                      text[:1024])
            text = text[1024:]
        if m.photo is not None:
            await self.api.send_media(to, "image", m.photo, "image/jpeg", "image.jpg",
                                      text[:1024])
            text = text[1024:]
        flat = [b for row in m.buttons for b in row]
        if flat and len(flat) <= 3:
            await self.api.send_buttons(to, text or "⬆️", flat)
        elif text.strip():
            if flat:                            # more than 3: name them as text
                text += "\n" + "\n".join(f"• {title}" for title, _ in flat)
            await self.api.send_text(to, text)

    async def _fallback(self, chat_id: str, message: OutgoingMessage, why: str) -> None:
        _log.warning(f"whatsapp → telegram fallback: {why}",
                     extra={"action": "whatsapp.fallback"})
        if self.fallback is not None:
            await self.fallback(chat_id, OutgoingMessage(
                "📱 (WhatsApp-এ পাঠানো যায়নি — তাই এখানে)\n" + message.text, message.buttons,
                message.photo, message.voice, message.task_id, message.video))
