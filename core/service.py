"""Core Service (plan §4): Telegram receive, task queue + engine, command
handling, basic health. A failed DB migration puts the service in SAFE MODE:
Telegram + /status stay alive, task intake is refused, the owner is alerted.

    uv run python workers/core-service/run.py

Exit codes: 0 = clean stop, 2 = invalid config / missing secrets,
3 = Telegram rejected the bot token.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import time
from collections.abc import Callable

import psutil

from channels.base import CallbackReply, IncomingCallback, IncomingMessage, OutgoingMessage
from channels.telegram.alerts import TelegramAlertHandler
from channels.telegram.api import TelegramAPI, TelegramAuthError, TelegramError
from channels.telegram.auth import ChatWhitelist
from channels.telegram.channel import TelegramChannel
from core.bootstrap import EXIT_INVALID_CONFIG, EXIT_OK, AppContext, bootstrap
from core.config import ConfigError
from core.db.engine import make_sessionmaker
from core.db.migrate import MigrationError, check_and_migrate
from core.ipc.client import WorkerClient
from core.ipc.monitor import WorkerMonitor
from core.ipc.token import TokenStore
from core.log import get_logger, redact, shutdown_logging
from core.log.setup import ROOT_LOGGER
from core.notify.notifier import MessageType, Notifier
from core.orchestrator.commands import CommandRouter, HealthSources
from core.orchestrator.executor import UserRequestExecutor
from core.orchestrator.screen_commands import ScreenCommands
from core.orchestrator.security_commands import SecurityCommands
from core.orchestrator.skill_commands import SkillCommands
from core.orchestrator.task_commands import TaskCommands
from core.permissions.approvals import ApprovalManager
from core.permissions.audit import AuditTrail
from core.permissions.engine import PermissionEngine
from core.queue.desktop_watch import DesktopWatcher
from core.queue.engine import TaskEngine
from core.queue.store import TaskStore, TaskView
from core.router.intent import IntentRouter
from core.skills.api import ToolEnv
from core.skills.paths import PathPolicy
from core.skills.registry import SkillError, SkillRegistry
from core.skills.runner import SkillRunner
from core.voice.correct import suggest as suggest_correction
from core.voice.intake import CALLBACK_PREFIX as VOICE_PREFIX
from core.voice.intake import VoiceIntake
from core.voice.transcriber import Transcriber
from core.voice.tts import GeminiTTS, Speaker, TTSBudget
from models.router import ProviderRouter
from providers.provider_base import USABLE
from providers.registry import ProviderRegistry

EXIT_TELEGRAM_AUTH = 3
APPROVAL_SWEEP_SECONDS = 15
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


def database_probe(store: TaskStore | None, revision: str | None,
                   safe_reason: str | None) -> Callable[[], tuple[bool, str]]:
    def probe() -> tuple[bool, str]:
        if store is None:
            return False, f"SAFE MODE — {safe_reason}"
        paused = " · queue PAUSED" if store.is_queue_paused() else ""
        return True, f"ok (schema {revision}){paused}"
    return probe


async def _announce_recovery(notifier: Notifier, recovered: list[TaskView]) -> None:
    by_chat: dict[str, list[TaskView]] = {}
    for t in recovered:
        by_chat.setdefault(t.chat_id, []).append(t)
    for chat_id, items in by_chat.items():
        lines = ["♻️ Restart-এর পরে recovery:"]
        lines += [f"#{t.id} {t.state} — {t.title}" for t in items]
        await notifier.notify(chat_id, MessageType.RECOVERY_AFTER_RESTART, "\n".join(lines))


async def run_core_service(
    ctx: AppContext, stop: asyncio.Event, api: TelegramAPI, whitelist: ChatWhitelist,
    provider_registry: ProviderRegistry | None = None,
) -> None:
    me = await api.get_me()   # fails fast on a bad token
    _log.info(f"telegram bot @{me.get('username')} authenticated",
              extra={"action": "telegram.getMe", "status": "ok"})
    cfg = ctx.config

    async def handle(msg: IncomingMessage) -> OutgoingMessage | None:
        _record(store, msg.channel, msg.chat_id, "in",
                msg.text or ("<voice>" if msg.voice else ""), msg.message_id)
        reply: OutgoingMessage | None
        if msg.voice is not None:
            reply = await voice.handle(msg, channel.api.download_file)
        else:
            reply = await router.handle(msg)
        if reply is not None:
            _record(store, msg.channel, msg.chat_id, "out", reply.text, None)
        return reply

    channel = TelegramChannel(api, whitelist, handle)
    # Attach the alert handler first so a failed migration is alerted too.
    alert = TelegramAlertHandler(channel, asyncio.get_running_loop())
    logging.getLogger(ROOT_LOGGER).addHandler(alert)
    notifier = Notifier(cfg.default.notification_throttle, channel.send)

    store: TaskStore | None = None
    engine: TaskEngine | None = None
    tasks: TaskCommands | None = None
    security: SecurityCommands | None = None
    approvals: ApprovalManager | None = None
    safe_reason: str | None = None
    revision: str | None = None
    db_engine = None
    try:
        mig = check_and_migrate(cfg.path("database"), cfg.path("backups_dir"))
        db_engine, revision = mig.engine, mig.after
        sessions = make_sessionmaker(mig.engine)
        store = TaskStore(sessions)
        audit = AuditTrail(sessions)
        permissions = PermissionEngine(cfg.permissions, sessions)
        approvals = ApprovalManager(sessions, cfg.permissions.approval, store, audit, notifier)
        engine = TaskEngine(store, notifier, cfg.default.task_engine, permissions, approvals)
        tasks = TaskCommands(store, engine, cfg.default.task_engine.list_limit,
                             cfg.default.agent.timezone)
        security = SecurityCommands(permissions, approvals, store, audit)
    except MigrationError as e:
        safe_reason = str(e) + (" (backup restored)" if e.rolled_back else "")
        _log.critical(f"DATABASE UNAVAILABLE — SAFE MODE: {safe_reason}",
                      extra={"action": "db.migrate", "status": "safe_mode",
                             "error_code": "DB_MIGRATION_FAILED"})

    # Skills load at startup; a broken enabled skill fails closed (not loaded)
    # but Telegram stays alive and the owner is alerted.
    registry: SkillRegistry | None = None
    skills_error = ""
    try:
        registry = SkillRegistry.load(cfg.skills)
    except SkillError as e:
        skills_error = str(e)
        _log.critical(f"SKILLS UNAVAILABLE: {e}", extra={"action": "skills.load",
                                                         "status": "error"})
    skill_cmds = (SkillCommands(registry, security.permissions if security else None)
                  if registry is not None else None)

    monitor = build_worker_monitor(ctx)
    desktop = next((c for c in monitor.clients if c.name == "desktop"), None)
    browser = next((c for c in monitor.clients if c.name == "browser"), None)
    tool_workers = {n: c for n, c in (("desktop", desktop), ("browser", browser)) if c}

    # Provider layer (plan §8): no single "main AI" — the router picks per task type.
    providers = provider_registry or ProviderRegistry.from_config(cfg, ctx.secrets)
    provider_router = ProviderRouter(cfg.providers, providers.adapters)
    if engine is not None and security is not None and approvals is not None:
        skill_runner = (SkillRunner(registry, ToolEnv(cfg, PathPolicy(cfg), tool_workers),
                                    approvals.audit) if registry is not None else None)
        engine.register("user_request", UserRequestExecutor(
            IntentRouter(provider_router), provider_router, skill_runner))

    # Voice (plan §7): transcript → the same path as typed text.
    wcfg = cfg.models.whisper
    whisper_path = wcfg.path if wcfg.path.is_absolute() else cfg.root / wcfg.path
    transcriber = Transcriber(whisper_path, wcfg.device, wcfg.compute_type,
                              cfg.resources.idle_unload_seconds.whisper,
                              cfg.default.voice.beam_size,
                              hint_words=cfg.default.voice.hint_words,
                              vad_speech_pad_ms=cfg.default.voice.vad_speech_pad_ms)
    speaker = build_speaker(ctx)
    voice = VoiceIntake(transcriber, cfg.default.voice, cfg.path("workspace_dir") / "voice",
                        lambda m: router.handle(m),
                        corrector=lambda heard, lang: suggest_correction(
                            provider_router, heard, lang, cfg.default.voice.hint_words),
                        speaker=speaker)

    async def speak_final_answer(chat_id: str, kind: MessageType, task_id: int) -> None:
        """Voice mode (plan §57): a task started by voice gets its answer spoken."""
        if task_id not in voice.voice_tasks or store is None:
            return
        voice.voice_tasks.discard(task_id)
        task = store.get(task_id)
        done = kind is MessageType.TASK_COMPLETED and task is not None and task.result_summary
        say = task.result_summary if done and task else "দুঃখিত, কাজটা শেষ করা যায়নি।"
        audio = await speaker.synthesize(str(say))
        if audio is not None:
            await channel.send(chat_id, OutgoingMessage("", voice=audio))

    notifier.on_task_final.append(speak_final_answer)

    async def on_button(cb: IncomingCallback) -> CallbackReply | None:
        if cb.data.startswith(f"{VOICE_PREFIX}:"):
            return await voice.on_button(cb)
        return await security.on_button(cb) if security is not None else None

    channel.callback_handler = on_button

    health = HealthSources()
    router = CommandRouter(cfg.root, health, cfg.default.agent.name, tasks, safe_reason,
                           security, skill_cmds, ScreenCommands(desktop))
    health.probes.update(build_probes(ctx, channel))
    health.probes["Database"] = database_probe(store, revision, safe_reason)
    health.probes["Skills"] = lambda: (
        (True, f"{len(registry.skills)} loaded") if registry is not None
        else (False, f"not loaded — {skills_error}"))
    try:
        await api.set_my_commands(router.menu())
    except TelegramError as e:   # menu is a convenience; never block startup on it
        _log.warning(f"setMyCommands failed: {e}", extra={"action": "telegram.menu"})

    if store is not None:
        recovered = store.recover_after_restart()
        if recovered:
            await _announce_recovery(notifier, recovered)

    for c in monitor.clients:
        health.probes[f"{c.name.capitalize()} worker"] = monitor.probe(c.name)
    health.probes["Privileged broker"] = lambda: (True, "not built yet (Phase 22) — disabled")
    health.probes["AI providers"] = lambda: provider_summary(provider_router)

    _log.info("core service started", extra={"action": "service.start", "status": "ok"})
    background = [asyncio.create_task(monitor.run(stop)),
                  asyncio.create_task(_provider_health_loop(providers, stop)),
                  asyncio.create_task(transcriber.idle_loop(stop))]
    if engine is not None and store is not None:
        background.append(asyncio.create_task(engine.run(stop)))
        watcher = DesktopWatcher(store, engine, desktop, notifier)
        background.append(asyncio.create_task(watcher.run(stop)))
    if approvals is not None:
        background.append(asyncio.create_task(_expiry_sweeper(approvals, stop)))
    try:
        await channel.run(stop)
    finally:
        stop.set()
        # Bounded wait (plan §32A forced-shutdown timeout); full procedure in Phase 23.
        _, pending = await asyncio.wait(background,
                                        timeout=cfg.default.shutdown.graceful_timeout_seconds)
        for t in pending:
            t.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        logging.getLogger(ROOT_LOGGER).removeHandler(alert)
        await monitor.aclose()
        await providers.aclose()
        await api.aclose()
        if db_engine is not None:
            db_engine.dispose()
        _log.info("core service stopped", extra={"action": "service.stop", "status": "ok"})


def build_speaker(ctx: AppContext) -> Speaker:
    """Natural Gemini voice when a GEMINI_API_KEY is configured, else Microsoft edge."""
    tts = ctx.config.default.voice.tts
    key = ctx.secrets.get("GEMINI_API_KEY")
    model = ctx.config.models.alias_sets.get("gemini_api", {}).get("tts")
    gemini = (GeminiTTS(key, model, tts.gemini_voice, tts.gemini_style)
              if key is not None and model else None)
    budget = TTSBudget(ctx.config.path("data_dir") / "tts_budget.json", tts.daily_char_budget)
    if tts.engine == "gemini" and gemini is None:
        _log.info("gemini voice not configured (no GEMINI_API_KEY) — using edge voice",
                  extra={"action": "voice.tts"})
    return Speaker(tts, gemini, budget)


def provider_summary(router: ProviderRouter) -> tuple[bool, str]:
    """At least one routable provider must be usable (plan §9: one provider
    failing must never stop the whole agent)."""
    usable = [n for n, a in router.adapters.items()
              if a.health.state in USABLE and router.capabilities.routable(n)]
    parts = []
    for name, a in router.adapters.items():
        short = name.split("_")[0] if name != "ollama_local" else "ollama"
        parts.append(f"{short}={a.health.state.value.lower()}")
    return bool(usable), " · ".join(parts)


async def _provider_health_loop(providers: ProviderRegistry, stop: asyncio.Event,
                                every: float = 60.0) -> None:
    while not stop.is_set():
        try:
            await providers.check_all()
        except Exception:
            _log.exception("provider health check failed", extra={"action": "provider.health"})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every)


def build_worker_monitor(ctx: AppContext) -> WorkerMonitor:
    wcfg = ctx.config.workers
    tokens = TokenStore(ctx.config.path("secrets_dir"))
    tokens.ensure()
    clients = [
        WorkerClient(name, w.host, w.port, tokens, wcfg.protocol_version)
        for name, w in (("desktop", wcfg.workers.desktop_worker),
                        ("browser", wcfg.workers.browser_worker))
        if w.enabled
    ]
    return WorkerMonitor(clients)


async def _expiry_sweeper(approvals: ApprovalManager, stop: asyncio.Event,
                          every: float = APPROVAL_SWEEP_SECONDS) -> None:
    while not stop.is_set():
        try:
            await approvals.expire_due()
        except Exception:
            _log.exception("approval expiry sweep failed", extra={"action": "approval.expire"})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every)


def _record(store: TaskStore | None, channel: str, chat_id: str, direction: str,
            text: str, external_id: str | None) -> None:
    if store is None:
        return
    try:
        store.record_message(channel=channel, chat_id=chat_id, direction=direction,
                             text=redact(text), external_id=external_id)
    except Exception:
        _log.exception("could not record message", extra={"action": "db.message"})


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
