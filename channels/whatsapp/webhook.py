"""Local webhook for the WhatsApp Cloud API (Phase 21). Listens on 127.0.0.1 only;
Meta reaches it through the Cloudflare tunnel.

GET  /whatsapp/webhook  Meta's verification: hub.verify_token must match ours
POST /whatsapp/webhook  events: the X-Hub-Signature-256 HMAC (App Secret) must match
                        the raw body, else 401 — nothing unsigned is ever processed.
Events are acknowledged at once (Meta retries slow answers) and handled after."""

from __future__ import annotations

import asyncio
import hmac
import json
from typing import Any

from fastapi import FastAPI, Request, Response
from pydantic import SecretStr

from channels.whatsapp.channel import WhatsAppChannel, valid_signature
from core.log import get_logger

_log = get_logger("core")
_audit = get_logger("audit")
PATH = "/whatsapp/webhook"
MAX_BODY = 1024 * 1024


def create_app(channel: WhatsAppChannel, app_secret: SecretStr,
               verify_token: SecretStr) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    pending: set[asyncio.Task[Any]] = set()

    @app.get(PATH)
    async def verify(request: Request) -> Response:
        q = request.query_params
        token = q.get("hub.verify_token") or ""
        if q.get("hub.mode") == "subscribe" and hmac.compare_digest(
                token.encode(), verify_token.get_secret_value().encode()):
            _log.info("whatsapp webhook verified by Meta", extra={"action": "whatsapp.verify",
                                                                  "status": "ok"})
            return Response(q.get("hub.challenge", ""), media_type="text/plain")
        _audit.warning("whatsapp webhook verification refused",
                       extra={"action": "whatsapp.verify", "status": "denied"})
        return Response(status_code=403)

    @app.post(PATH)
    async def receive(request: Request) -> Response:
        body = await request.body()
        if len(body) > MAX_BODY:
            return Response(status_code=413)
        if not valid_signature(app_secret, body, request.headers.get("x-hub-signature-256")):
            _audit.warning("whatsapp webhook with a bad signature refused",
                           extra={"action": "whatsapp.signature", "status": "denied"})
            return Response(status_code=401)
        try:
            payload = json.loads(body)
        except ValueError:
            return Response(status_code=400)
        task = asyncio.create_task(channel.on_webhook(payload))
        pending.add(task)
        task.add_done_callback(_done(pending))
        return Response(status_code=200)

    return app


def _done(pending: set[asyncio.Task[Any]]):  # type: ignore[no-untyped-def]
    def cb(task: asyncio.Task[Any]) -> None:
        pending.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _log.error(f"whatsapp event handling failed: {task.exception()!r}",
                       extra={"action": "whatsapp.handle", "status": "error"})
    return cb
