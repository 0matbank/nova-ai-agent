"""WhatsApp Business Cloud API client (official, Meta Graph API — Phase 21, owner
choice 2026-10-16). Only the calls Nova needs: send text / reply buttons / media,
download a voice note, mark read, register the webhook. The access token stays in
secrets/.env and is never logged."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import SecretStr

GRAPH = "https://graph.facebook.com"
MAX_TEXT = 4000                      # WhatsApp allows 4096; keep a margin
MAX_BUTTONS = 3                      # interactive reply buttons
MAX_BUTTON_TITLE = 20
WINDOW_CLOSED = {131047, 131026}     # outside the 24 h customer-service window / undeliverable
AUTH_CODES = {190, 10, 200}


class WhatsAppError(Exception):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class WhatsAppWindowClosed(WhatsAppError):
    """Meta only allows free-form messages within 24 h of the owner's last message."""


class WhatsAppAuthError(WhatsAppError):
    pass


def chunk(text: str, size: int = MAX_TEXT) -> list[str]:
    if len(text) <= size:
        return [text]
    parts, rest = [], text
    while rest:
        cut = rest.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return parts


class WhatsAppAPI:
    def __init__(self, token: SecretStr, phone_number_id: str, version: str = "v21.0",
                 client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self.phone_number_id = phone_number_id
        self.base = f"{GRAPH}/{version}"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token.get_secret_value()}"}

    @staticmethod
    def _raise(r: httpx.Response) -> None:
        try:
            err = (r.json() or {}).get("error") or {}
        except ValueError:
            err = {}
        code = err.get("code")
        sub = (err.get("error_data") or {}).get("details") or err.get("message") or r.text[:200]
        msg = f"WhatsApp HTTP {r.status_code} code {code}: {sub}"
        if code in WINDOW_CLOSED:
            raise WhatsAppWindowClosed(msg, code)
        if code in AUTH_CODES or r.status_code == 401:
            raise WhatsAppAuthError(msg, code)
        raise WhatsAppError(msg, code)

    async def _post(self, path: str, **kw: Any) -> dict[str, Any]:
        try:
            r = await self._client.post(f"{self.base}/{path}", headers=self._headers(), **kw)
        except httpx.HTTPError as e:
            raise WhatsAppError(f"WhatsApp network error: {type(e).__name__}") from None
        if r.status_code >= 300:
            self._raise(r)
        data: dict[str, Any] = r.json()
        return data

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        try:
            r = await self._client.get(f"{self.base}/{path}", headers=self._headers(),
                                       params=params)
        except httpx.HTTPError as e:
            raise WhatsAppError(f"WhatsApp network error: {type(e).__name__}") from None
        if r.status_code >= 300:
            self._raise(r)
        data: dict[str, Any] = r.json()
        return data

    # ------------------------------------------------------------------ send

    async def _message(self, to: str, body: dict[str, Any]) -> str:
        data = await self._post(f"{self.phone_number_id}/messages", json={
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **body})
        return str((data.get("messages") or [{}])[0].get("id", ""))

    async def send_text(self, to: str, text: str) -> list[str]:
        return [await self._message(to, {"type": "text", "text": {"body": part,
                                                                  "preview_url": False}})
                for part in chunk(text)]

    async def send_buttons(self, to: str, text: str, buttons: list[tuple[str, str]]) -> str:
        """Up to 3 reply buttons: (title, id). Titles are cut to 20 characters."""
        return await self._message(to, {"type": "interactive", "interactive": {
            "type": "button", "body": {"text": text[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {
                "id": bid[:256], "title": title[:MAX_BUTTON_TITLE]}}
                for title, bid in buttons[:MAX_BUTTONS]]}}})

    async def upload_media(self, data: bytes, mime: str, filename: str) -> str:
        r = await self._post(f"{self.phone_number_id}/media",
                             data={"messaging_product": "whatsapp", "type": mime},
                             files={"file": (filename, data, mime)})
        return str(r["id"])

    async def send_media(self, to: str, kind: str, data: bytes, mime: str, filename: str,
                         caption: str = "") -> str:
        media_id = await self.upload_media(data, mime, filename)
        body: dict[str, Any] = {"id": media_id}
        if caption and kind in ("image", "video", "document"):
            body["caption"] = caption[:1024]
        return await self._message(to, {"type": kind, kind: body})

    async def mark_read(self, message_id: str) -> None:
        await self._post(f"{self.phone_number_id}/messages", json={
            "messaging_product": "whatsapp", "status": "read", "message_id": message_id})

    # --------------------------------------------------------------- receive

    async def download_media(self, media_id: str, max_bytes: int = 20 * 1024 * 1024) -> bytes:
        meta = await self._get(media_id)
        url = str(meta.get("url", ""))
        if not url.startswith("https://"):
            raise WhatsAppError("media URL missing")
        if int(meta.get("file_size") or 0) > max_bytes:
            raise WhatsAppError("media too large")
        try:
            r = await self._client.get(url, headers=self._headers())
        except httpx.HTTPError as e:
            raise WhatsAppError(f"media download failed: {type(e).__name__}") from None
        if r.status_code >= 300:
            self._raise(r)
        if len(r.content) > max_bytes:
            raise WhatsAppError("media too large")
        return r.content

    # --------------------------------------------------------------- setup

    async def check(self) -> dict[str, Any]:
        """The phone number behind phone_number_id (proves the token works)."""
        return await self._get(self.phone_number_id,
                               fields="display_phone_number,verified_name,quality_rating")

    async def register_webhook(self, app_id: str, app_secret: SecretStr, callback_url: str,
                               verify_token: SecretStr) -> None:
        """Point the app's WhatsApp webhook at `callback_url` (Meta calls it back with
        the verify token first). Uses the app access token app_id|app_secret."""
        try:
            r = await self._client.post(f"{self.base}/{app_id}/subscriptions", data={
                "object": "whatsapp_business_account", "callback_url": callback_url,
                "verify_token": verify_token.get_secret_value(), "fields": "messages",
                "include_values": "true",
                "access_token": f"{app_id}|{app_secret.get_secret_value()}"})
        except httpx.HTTPError as e:
            raise WhatsAppError(f"webhook registration failed: {type(e).__name__}") from None
        if r.status_code >= 300:
            self._raise(r)

    async def subscribe_app(self, waba_id: str) -> None:
        """Let the WhatsApp Business Account send its events to this app."""
        await self._post(f"{waba_id}/subscribed_apps")
