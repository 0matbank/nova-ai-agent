"""Core Service (plan §4): Telegram receive + command handling + basic health.

    uv run python workers/core-service/run.py

Exit codes: 0 = clean stop, 2 = invalid config / missing secrets,
3 = Telegram rejected the bot token.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from collections.abc import Callable

import psutil

from channels.telegram.alerts import TelegramAlertHandler
from channels.telegram.api import TelegramAPI, TelegramAuthError, TelegramError
from channels.telegram.auth import ChatWhitelist
from channels.telegram.channel import TelegramChannel
from core.bootstrap import EXIT_INVALID_CONFIG, EXIT_OK, AppContext, bootstrap
from core.config import ConfigError
from core.log import get_logger, shutdown_logging
from core.log.setup import ROOT_LOGGER
from core.orchestrator.commands import CommandRouter, HealthSources

EXIT_TELEGRAM_AUTH = 3
TOKEN_KEY = "TELEGRAM_BOT_TOKEN"  # noqa: S105 - env var name, not a secret
WHITELIST_KEY = "TELEGRAM_ALLOWED_CHAT_IDS"

_log = get_logger("core")


def telegram_settings(ctx: AppContext) -> tuple[TelegramAPI, ChatWhitelist]:
    token = ctx.secrets.get(TOKEN_KEY)
    ids = ctx.secrets.get(WHITELIST_KEY)
    env = ctx.config.path("secrets_dir") / ".env"
    errors = []
    if token is None:
        errors.append(f"{TOKEN_KEY} is not set in {env}")
    if ids is None:
        errors.append(f"{WHITELIST_KEY} is not set in {env} (no one would be authorized)")
    if errors:
        raise ConfigError(errors)
    assert token is not None and ids is not None
    try:
        whitelist = ChatWhitelist.from_csv(ids.get_secret_value())
    except ValueError as e:
        raise ConfigError([str(e)]) from None
    if not len(whitelist):
        raise ConfigError([f"{WHITELIST_KEY} is empty"])
    return TelegramAPI(token), whitelist


def build_probes(
    ctx: AppContext, channel: TelegramChannel
) -> dict[str, Callable[[], tuple[bool, str]]]:
    min_free_gb = ctx.config.resources.disk_free_gb_min
    root = ctx.config.root

    def telegram() -> tuple[bool, str]:
        if not channel.connected or channel.last_poll_ok is None:
            return False, "disconnected"
        age = time.time() - channel.last_poll_ok
        return age < channel.poll_timeout + 30, f"connected (last poll {age:.0f}s ago)"

    def disk() -> tuple[bool, str]:
        free = psutil.disk_usage(str(root)).free / 1024 ** 3
        return free >= min_free_gb, f"{free:.0f} GB free (min {min_free_gb})"

    def config() -> tuple[bool, str]:
        return True, "valid"

    return {"Telegram": telegram, "Config": config, "Disk": disk}


async def run_core_service(
    ctx: AppContext, stop: asyncio.Event, api: TelegramAPI, whitelist: ChatWhitelist
) -> None:
    me = await api.get_me()   # fails fast on a bad token
    _log.info(f"telegram bot @{me.get('username')} authenticated",
              extra={"action": "telegram.getMe", "status": "ok"})

    health = HealthSources()
    router = CommandRouter(ctx.config.root, health, ctx.config.default.agent.name)
    channel = TelegramChannel(api, whitelist, router.handle)
    health.probes.update(build_probes(ctx, channel))
    try:
        await api.set_my_commands(router.menu())
    except TelegramError as e:   # menu is a convenience; never block startup on it
        _log.warning(f"setMyCommands failed: {e}", extra={"action": "telegram.menu"})

    alert = TelegramAlertHandler(channel, asyncio.get_running_loop())
    logging.getLogger(ROOT_LOGGER).addHandler(alert)
    _log.info("core service started", extra={"action": "service.start", "status": "ok"})
    try:
        await channel.run(stop)
    finally:
        logging.getLogger(ROOT_LOGGER).removeHandler(alert)
        await api.aclose()
        _log.info("core service stopped", extra={"action": "service.stop", "status": "ok"})


def main() -> int:
    try:
        ctx = bootstrap(worker="core")
        api, whitelist = telegram_settings(ctx)
    except ConfigError as e:
        print(f"STARTUP REFUSED - {e}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    async def _amain() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _request_stop(*_: object) -> None:
            loop.call_soon_threadsafe(stop.set)

        signal.signal(signal.SIGINT, _request_stop)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _request_stop)
        try:
            await run_core_service(ctx, stop, api, whitelist)
        except TelegramAuthError:
            return EXIT_TELEGRAM_AUTH
        except TelegramError as e:
            _log.error(f"telegram startup failed: {e}", extra={"action": "service.start",
                                                              "status": "error"})
            return EXIT_TELEGRAM_AUTH
        return EXIT_OK

    try:
        return asyncio.run(_amain())
    finally:
        shutdown_logging()
