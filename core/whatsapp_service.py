"""Start the WhatsApp channel inside the core service (Phase 21): the Graph API
client, the local webhook (127.0.0.1), the Cloudflare tunnel and the webhook
registration with Meta. Off unless config/default.yaml whatsapp.enabled and every
secret is present; a failure here never stops Telegram."""

from __future__ import annotations

import asyncio
import contextlib
import secrets as pysecrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import SecretStr

from channels.base import CallbackHandler, MessageHandler, OutgoingMessage
from channels.whatsapp.api import WhatsAppAPI, WhatsAppError
from channels.whatsapp.channel import WhatsAppChannel
from channels.whatsapp.tunnel import QuickTunnel
from channels.whatsapp.webhook import PATH, create_app
from core.config.schema import AppConfig
from core.config.secrets import Secrets
from core.log import add_known_secrets, get_logger

_log = get_logger("core")
VERIFY_FILE = "whatsapp_verify.token"      # noqa: S105 - a file name, not a secret
REQUIRED = ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_APP_ID",
            "WHATSAPP_APP_SECRET", "WHATSAPP_ALLOWED_NUMBERS")
Broadcast = Callable[[OutgoingMessage], Awaitable[None]]


def verify_token(secrets_dir: Path) -> SecretStr:
    """Nova's own webhook verify token, generated once into the secrets folder."""
    path = secrets_dir / VERIFY_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pysecrets.token_urlsafe(32), encoding="utf-8")
    value = path.read_text(encoding="utf-8").strip()
    add_known_secrets([value])
    return SecretStr(value)


def missing_secrets(sec: Secrets) -> list[str]:
    missing = []
    for key in REQUIRED:
        value = sec.get(key)
        if value is None or not value.get_secret_value().strip():
            missing.append(key)
    return missing


async def start_whatsapp(cfg: AppConfig, sec: Secrets, handle: MessageHandler,
                         on_button: CallbackHandler, telegram_broadcast: Broadcast,
                         stop: asyncio.Event) -> tuple[WhatsAppChannel | None,
                                                       list[asyncio.Task[Any]]]:
    wcfg = cfg.default.whatsapp
    if not wcfg.enabled:
        return None, []
    missing = missing_secrets(sec)
    if missing:
        _log.critical("WhatsApp is enabled but secrets/.env lacks: " + ", ".join(missing),
                      extra={"action": "whatsapp.start", "status": "error"})
        return None, []

    def s(key: str) -> SecretStr:
        v = sec.get(key)
        assert v is not None
        return v
    # never printable in logs or tool output
    add_known_secrets([s("WHATSAPP_ACCESS_TOKEN").get_secret_value(),
                       s("WHATSAPP_APP_SECRET").get_secret_value()])
    api = WhatsAppAPI(s("WHATSAPP_ACCESS_TOKEN"), s("WHATSAPP_PHONE_NUMBER_ID")
                      .get_secret_value().strip(), wcfg.graph_version)
    try:
        info = await api.check()
    except WhatsAppError as e:
        _log.critical(f"WhatsApp sign-in failed: {e}", extra={"action": "whatsapp.start",
                                                              "status": "auth_failed"})
        await api.aclose()
        return None, []
    _log.info(f"whatsapp number {info.get('display_phone_number')} ready",
              extra={"action": "whatsapp.start", "status": "ok"})

    async def fallback(chat_id: str, out: OutgoingMessage) -> None:
        await telegram_broadcast(out)

    allowed = s("WHATSAPP_ALLOWED_NUMBERS").get_secret_value().split(",")
    channel = WhatsAppChannel(api, allowed, handle, on_button, fallback)
    token = verify_token(cfg.path("secrets_dir"))
    app_secret = s("WHATSAPP_APP_SECRET")
    server = uvicorn.Server(uvicorn.Config(create_app(channel, app_secret, token),
                                           host="127.0.0.1", port=wcfg.webhook_port,
                                           log_level="warning", access_log=False))
    tasks: list[asyncio.Task[Any]] = [asyncio.create_task(server.serve())]

    async def stopper() -> None:
        await stop.wait()
        server.should_exit = True
        await api.aclose()
    tasks.append(asyncio.create_task(stopper()))
    waba = sec.get("WHATSAPP_BUSINESS_ACCOUNT_ID")

    async def on_url(url: str) -> None:
        for _ in range(50):                          # the webhook must answer Meta's check
            if server.started:
                break
            await asyncio.sleep(0.2)
        try:
            await api.register_webhook(s("WHATSAPP_APP_ID").get_secret_value().strip(),
                                       app_secret, url.rstrip("/") + PATH, token)
            if waba is not None and waba.get_secret_value().strip():
                with contextlib.suppress(WhatsAppError):
                    await api.subscribe_app(waba.get_secret_value().strip())
            channel.public_url = url
            _log.info("whatsapp webhook registered with Meta",
                      extra={"action": "whatsapp.webhook", "status": "ok"})
        except WhatsAppError as e:
            _log.critical(f"WhatsApp webhook registration failed: {e}",
                          extra={"action": "whatsapp.webhook", "status": "error"})

    if wcfg.tunnel == "fixed" and wcfg.public_url:
        tasks.append(asyncio.create_task(on_url(wcfg.public_url)))
    else:
        exe = cfg.root / wcfg.cloudflared
        if not exe.is_file():
            _log.critical(f"cloudflared not found at {exe} — run scripts/whatsapp_setup.py",
                          extra={"action": "whatsapp.tunnel", "status": "error"})
        else:
            tunnel = QuickTunnel(exe, wcfg.webhook_port, on_url)
            tasks.append(asyncio.create_task(tunnel.run(stop)))
    return channel, tasks
