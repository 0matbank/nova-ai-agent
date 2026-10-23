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
from typing import Any

import psutil

from channels.base import CallbackReply, IncomingCallback, IncomingMessage, OutgoingMessage
from channels.telegram.alerts import TelegramAlertHandler
from channels.telegram.api import TelegramAPI, TelegramAuthError, TelegramError
from channels.telegram.auth import ChatWhitelist
from channels.telegram.channel import TelegramChannel
from channels.whatsapp.channel import PREFIX as WA_PREFIX
from channels.whatsapp.channel import WhatsAppChannel
from core.agents import Roster
from core.apps.resolver import AppResolver
from core.apps.service import build_app_resolver
from core.bootstrap import EXIT_INVALID_CONFIG, AppContext, bootstrap
from core.broker.client import BrokerClient
from core.broker.watch import BrokerWatch
from core.config import ConfigError
from core.config.schema import AppConfig
from core.dashboard.api import DashboardDeps, dashboard_token, ollama_loaded, start_dashboard
from core.db.engine import make_sessionmaker
from core.db.migrate import MigrationError, check_and_migrate
from core.ipc.client import WorkerClient
from core.ipc.monitor import WorkerMonitor
from core.ipc.token import TokenStore
from core.lifecycle import (
    EXIT_RESTART,
    EXIT_UPDATE,
    ExitRequest,
    head_commit,
    supervised,
    watch_stop_file,
    write_health,
)
from core.log import get_logger, redact, shutdown_logging
from core.log.setup import ROOT_LOGGER
from core.memory import ProjectMemory
from core.notify.notifier import MessageType, Notifier
from core.orchestrator.agent_commands import AgentCommands
from core.orchestrator.commands import CommandRouter, HealthSources
from core.orchestrator.executor import UserRequestExecutor
from core.orchestrator.log_commands import LogCommands
from core.orchestrator.schedule_commands import ScheduleCommands
from core.orchestrator.screen_commands import ScreenCommands
from core.orchestrator.security_commands import SecurityCommands
from core.orchestrator.self_update import SELF_UPDATE, LifecycleCommands, SelfUpdateExecutor
from core.orchestrator.skill_commands import SkillCommands
from core.orchestrator.task_commands import TaskCommands
from core.permissions.approvals import ApprovalManager
from core.permissions.audit import AuditTrail
from core.permissions.engine import PermissionEngine
from core.queue.desktop_watch import DesktopWatcher
from core.queue.engine import TaskEngine
from core.queue.store import TaskStore, TaskView
from core.resources import ResourcePressure
from core.router.intent import IntentRouter, is_quick_request
from core.scheduler import Scheduler, ScheduleStore
from core.skills.api import ToolEnv
from core.skills.ledger import SideEffectLedger
from core.skills.paths import PathPolicy
from core.skills.registry import SkillError, SkillRegistry
from core.skills.runner import SkillRunner
from core.voice.correct import suggest as suggest_correction
from core.voice.intake import CALLBACK_PREFIX as VOICE_PREFIX
from core.voice.intake import VoiceIntake
from core.voice.transcriber import Transcriber
from core.voice.tts import GeminiTTS, Speaker, TTSBudget
from core.whatsapp_service import start_whatsapp
from models.router import ProviderRouter
from models.router.circuit import ProviderHistory
from providers.ollama_local import OllamaAdapter
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
        first = channel.polling_since
        if (channel.last_poll_ok is None and first is not None
                and time.time() - first < channel.poll_timeout + 30):
            # getMe passed at start; the first long-poll is still open (up to ~50 s)
            return True, "connected (first poll running)"
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


def engine_probe(engine: TaskEngine | None) -> Callable[[], tuple[bool, str]]:
    """Is the task dispatcher alive? (audit 2026-10-15: it could die silently)"""
    def probe() -> tuple[bool, str]:
        if engine is None:
            return False, "not running (safe mode)"
        if engine.queue_error:
            return False, f"cannot read the queue — {engine.queue_error}"
        if engine.current_task_id is not None:
            return True, f"working on task #{engine.current_task_id}"
        if engine.heartbeat is None:
            return True, "starting"
        idle = time.time() - engine.heartbeat
        limit = max(60.0, engine.settings.idle_poll_seconds * 5)
        return (idle < limit), ("idle, ready" if idle < limit else
                                f"no loop round for {int(idle)} s — stalled?")
    return probe


async def _announce_recovery(notifier: Notifier, recovered: list[TaskView]) -> None:
    by_chat: dict[str, list[TaskView]] = {}
    for t in recovered:
        by_chat.setdefault(t.chat_id, []).append(t)
    for chat_id, items in by_chat.items():
        lines = ["♻️ Restart-এর পরে recovery:"]
        lines += [f"#{t.id} {t.state} — {t.title}" for t in items]
        await notifier.notify(chat_id, MessageType.RECOVERY_AFTER_RESTART, "\n".join(lines))


async def _going_offline(channel: TelegramChannel, exiting: ExitRequest,
                         engine: TaskEngine | None) -> None:
    why = {EXIT_RESTART: "restart হচ্ছে", EXIT_UPDATE: "update হচ্ছে"}.get(
        exiting.code, "বন্ধ হচ্ছে")
    paused = engine.interrupted_ids if engine is not None else []
    text = (f"🔌 Nova {why} — কিছুক্ষণ offline।"
            + (f"\nচলমান task {', '.join(f'#{i}' for i in paused)} checkpoint-এ থেমেছে, "
               "ফিরে এসে সেখান থেকে চলবে।" if paused else "")
            + ("\nফিরে এলে জানাব।" if exiting.code in (EXIT_RESTART, EXIT_UPDATE) else ""))
    try:
        await asyncio.wait_for(channel.broadcast(OutgoingMessage(text)), timeout=10)
    except Exception:  # noqa: BLE001 - going offline must never hang on the network
        _log.warning("going-offline notice not sent", extra={"action": "service.stop"})


async def _get_me_when_online(api: TelegramAPI, stop: asyncio.Event) -> dict[str, Any] | None:
    """Phase 25 PC-restart finding: right after Windows login the network is often not
    up yet. A network error is not a bad token — wait for the network (backoff) instead
    of exiting; only TelegramAuthError ends the start."""
    delay, warned = 2.0, False
    while not stop.is_set():
        try:
            me: dict[str, Any] = await api.get_me()
            if warned:
                _log.info("network is up — Telegram reachable",
                          extra={"action": "telegram.getMe", "status": "ok"})
            return me
        except TelegramAuthError:
            raise
        except TelegramError as e:
            if not warned:
                _log.warning(f"Telegram not reachable yet ({e}) — waiting for the network",
                             extra={"action": "telegram.getMe", "status": "waiting"})
                warned = True
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)
            delay = min(delay * 2, 30.0)
    return None


async def run_core_service(
    ctx: AppContext, stop: asyncio.Event, api: TelegramAPI, whitelist: ChatWhitelist,
    provider_registry: ProviderRegistry | None = None,
    exit_request: ExitRequest | None = None,
) -> None:
    me = await _get_me_when_online(api, stop)   # a bad token still fails at once
    if me is None:                               # stopped while waiting for the network
        return
    _log.info(f"telegram bot @{me.get('username')} authenticated",
              extra={"action": "telegram.getMe", "status": "ok"})
    cfg = ctx.config

    async def handle(msg: IncomingMessage) -> OutgoingMessage | None:
        if store is not None and msg.message_id:
            try:
                seen = store.seen_inbound(msg.channel, msg.chat_id, str(msg.message_id))
            except Exception:  # noqa: BLE001 - a DB hiccup must not drop the message
                seen = False
            if seen:
                _log.warning(f"message {msg.message_id} delivered again — already handled, "
                             "ignored", extra={"action": "message.duplicate",
                                               "status": "ignored"})
                return None
        _record(store, msg.channel, msg.chat_id, "in",
                msg.text or ("<voice>" if msg.voice else ""), msg.message_id)
        reply: OutgoingMessage | None
        if msg.voice is not None:
            # Phase 21: a WhatsApp voice note is fetched from WhatsApp
            download = (wa_channel.download if msg.channel == "whatsapp" and wa_channel
                        else channel.api.download_file)
            reply = await voice.handle(msg, download)
        else:
            reply = await router.handle(msg)
        if reply is not None:
            _record(store, msg.channel, msg.chat_id, "out", reply.text, None, reply.task_id)
            if reply.task_id is not None and store is not None and msg.message_id:
                with contextlib.suppress(Exception):
                    store.link_message(msg.channel, msg.chat_id, str(msg.message_id),
                                       reply.task_id)
        return reply

    channel = TelegramChannel(api, whitelist, handle)
    # Attach the alert handler first so a failed migration is alerted too.
    alert = TelegramAlertHandler(channel, asyncio.get_running_loop())
    logging.getLogger(ROOT_LOGGER).addHandler(alert)
    wa_channel: WhatsAppChannel | None = None

    async def send_and_record(chat_id: str, out: OutgoingMessage) -> None:
        # Phase 21: a task that came in on WhatsApp is answered on WhatsApp
        if chat_id.startswith(WA_PREFIX) and wa_channel is not None:
            await wa_channel.send(chat_id, out)
            _record(store, "whatsapp", chat_id, "out", out.text, None, out.task_id)
            return
        if chat_id.startswith(WA_PREFIX):            # WhatsApp is off: tell the owner here
            await channel.broadcast(out)
            return
        await channel.send(chat_id, out)
        _record(store, "telegram", chat_id, "out", out.text, None, out.task_id)

    notifier = Notifier(cfg.default.notification_throttle, send_and_record)

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
    db_sessions = make_sessionmaker(db_engine) if db_engine is not None else None
    # Circuit breaker state + call history live in the DB (plan §8.6, §8.9).
    history = ProviderHistory(db_sessions) if db_sessions is not None else None
    pressure = ResourcePressure(cfg.resources, cfg.root)     # plan §17H
    provider_router = ProviderRouter(cfg.providers, providers.adapters, history, pressure)
    # Phase 16 roster + Phase 17 project memory (in the agent DB, survives restarts)
    roster = Roster.load(cfg)
    project_memory = ProjectMemory(db_sessions) if db_sessions is not None else None
    # Phase 19 scheduler: jobs in the DB, so they survive a restart (plan §39)
    schedule_store = ScheduleStore(db_sessions) if db_sessions is not None else None
    # V1.1: installed apps by spoken / misheard / learned name (catalog in data/)
    app_resolver = build_app_resolver(cfg, db_sessions)
    if tasks is not None:
        # V1.1 fast lane: quick deterministic requests never wait behind long AI work
        tasks.quick = lambda text: is_quick_request(text, app_resolver)
    if engine is not None and security is not None and approvals is not None:
        # plan §9.1: side effects go through the idempotency ledger
        skill_runner = (SkillRunner(registry, ToolEnv(cfg, PathPolicy(cfg), tool_workers),
                                    approvals.audit, SideEffectLedger(db_sessions))
                        if registry is not None and db_sessions is not None else None)
        engine.register("user_request", UserRequestExecutor(
            IntentRouter(provider_router, apps=app_resolver), provider_router, skill_runner,
            roster,
            project_memory, schedule_store))

    exiting = exit_request or ExitRequest()

    def request_exit(code: int, reason: str) -> None:
        """Phase 23: restart / update through the supervisor — no new task starts."""
        exiting.set(code, reason)
        if engine is not None:
            engine.draining = True
        stop.set()

    if engine is not None:
        engine.register(SELF_UPDATE, SelfUpdateExecutor(cfg, request_exit))

    # Voice (plan §7): transcript → the same path as typed text.
    transcriber = build_transcriber(cfg)
    transcriber.pressure = pressure                 # V1.1 warm-idle follows memory pressure
    pressure.relievers.append(transcriber.relieve)  # plan §17H unload_idle_whisper
    speaker = build_speaker(ctx)
    voice = VoiceIntake(transcriber, cfg.default.voice, cfg.path("workspace_dir") / "voice",
                        lambda m: router.handle(m),
                        corrector=lambda heard, lang: suggest_correction(
                            provider_router, heard, lang, cfg.default.voice.hint_words),
                        speaker=speaker, low_risk=app_resolver.is_low_risk,
                        send=channel.send)

    async def speak_final_answer(chat_id: str, kind: MessageType, task_id: int) -> None:
        """Voice mode (plan §57): a task started by voice gets its answer spoken."""
        if voice.finish(task_id) is None or store is None:
            return
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
                           security, skill_cmds, ScreenCommands(desktop),
                           LogCommands(db_sessions, cfg.default.agent.timezone)
                           if db_sessions is not None else None,
                           AgentCommands(cfg, roster, project_memory,
                                         cfg.default.agent.timezone),
                           ScheduleCommands(schedule_store, cfg.default.agent.timezone)
                           if schedule_store is not None else None)
    lifecycle = LifecycleCommands(store, engine, request_exit)
    router.add("/restart-agent", lifecycle.restart)
    router.add("/update", lifecycle.update)
    version = head_commit(cfg.app_dir)
    health.probes["Version"] = lambda: (True, f"{version[:8] or 'unknown'}"
                                        + (" · supervisor" if supervised() else ""))
    health.probes.update(build_probes(ctx, channel))
    health.probes["Database"] = database_probe(store, revision, safe_reason)
    health.probes["Task engine"] = engine_probe(engine)
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
    # Phase 22: the elevated allowlist-only broker (started by Task Scheduler)
    broker_watch = BrokerWatch(BrokerClient(cfg, TokenStore(cfg.path("secrets_dir"))))
    health.probes["Privileged broker"] = broker_watch.probe
    if security is not None:
        security.broker_lockdown = broker_watch.lockdown
    health.probes["AI providers"] = lambda: provider_summary(provider_router)
    # Phase 24: local dashboard on the same engines (127.0.0.1 only)
    dash_tasks, health.probes["Dashboard"] = start_dashboard(DashboardDeps(
        cfg=cfg, token=dashboard_token(cfg.path("secrets_dir")),
        owner_chat=(sorted(channel.whitelist.chat_ids) or [""])[0], version=version,
        store=store, engine=engine, approvals=approvals, sessions=db_sessions,
        security=security, probes=health.probes, router=provider_router, roster=roster,
        registry=registry, memory=project_memory, schedules=schedule_store,
        ollama_ps=ollama_loaded), cfg, stop)
    # Phase 21: WhatsApp (official Cloud API) — never blocks Telegram
    wa_channel, wa_tasks = await start_whatsapp(cfg, ctx.secrets, handle, on_button,
                                                channel.broadcast, stop)
    if cfg.default.whatsapp.enabled:
        health.probes["WhatsApp"] = lambda: (
            (False, "not running — see the log") if wa_channel is None else
            (True, "connected" + (" · webhook ok" if wa_channel.public_url else
                                  " · webhook pending")) if wa_channel.connected or
            wa_channel.public_url else (False, "webhook not registered yet"))

    _log.info("core service started", extra={"action": "service.start", "status": "ok"})
    # Phase 23: the supervisor's proof that this version is fully up
    write_health({"version": version}, cfg.app_dir)
    if supervised():
        # the counterpart of the going-offline notice (restart, update, crash, reboot)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(channel.broadcast(OutgoingMessage(
                f"🟢 Nova আবার online — version {version[:8]}")), timeout=10)
    background: list[asyncio.Task[Any]] = [*wa_tasks, *dash_tasks,
        asyncio.create_task(monitor.run(stop)),
        asyncio.create_task(_provider_health_loop(providers, stop, provider_router)),
        asyncio.create_task(transcriber.idle_loop(stop)),
        asyncio.create_task(refresh_apps(app_resolver, transcriber, cfg)),
        asyncio.create_task(pressure.run(stop)),
        asyncio.create_task(broker_watch.run(
            stop, lambda text: channel.broadcast(OutgoingMessage(text))))]
    local = providers.adapters.get("ollama_local")
    if isinstance(local, OllamaAdapter):
        pressure.relievers.append(local.relieve)       # plan §17H critical
        # owner 2026-10-16: an idle runner whose prompt cache grew gives the RAM back
        pressure.relievers.append(local.flush_if_bloated)
    if isinstance(local, OllamaAdapter) and local.policy.keep_default_loaded:
        # owner 2026-10-15: the quick-answer model is ready before the first question
        background.append(asyncio.create_task(local.warm()))
    if engine is not None and store is not None:
        background.append(asyncio.create_task(engine.run(stop)))
        background.append(asyncio.create_task(engine.run(stop, fast=True)))
        watcher = DesktopWatcher(store, engine, desktop, notifier)
        background.append(asyncio.create_task(watcher.run(stop)))
    if approvals is not None:
        background.append(asyncio.create_task(_expiry_sweeper(approvals, stop)))
    if schedule_store is not None and store is not None and engine is not None:
        scheduler = Scheduler(schedule_store, store, notifier, cfg.default.agent.timezone,
                              wake=engine.wake)
        owner = sorted(channel.whitelist.chat_ids)
        scheduler.sync_static(cfg.schedules.schedules, owner[0] if owner else None)
        background.append(asyncio.create_task(scheduler.run(stop)))
    try:
        await channel.run(stop)
    finally:
        # plan §32A graceful shutdown: 1–2 no new task / message (Telegram keeps new
        # messages until we are back), 3–6 the running task stops at its next checkpoint
        # (state is in the DB), then going-offline notice, AI drain, DB + logs closed
        stop.set()
        if engine is not None:
            engine.draining = True
        _, pending = await asyncio.wait(background,
                                        timeout=cfg.default.shutdown.graceful_timeout_seconds)
        for t in pending:
            t.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await _going_offline(channel, exiting, engine)
        logging.getLogger(ROOT_LOGGER).removeHandler(alert)
        await monitor.aclose()
        await providers.aclose()
        await api.aclose()
        if db_engine is not None:
            db_engine.dispose()
        _log.info("core service stopped", extra={"action": "service.stop", "status": "ok"})


async def refresh_apps(resolver: AppResolver, transcriber: Transcriber,
                       cfg: AppConfig) -> None:
    """V1.1: scan the installed apps in the background at start (a day-old catalog is
    rescanned; a missing name rescans on demand too), then give Whisper their names."""
    try:
        cat = await asyncio.to_thread(lambda: resolver.catalog)
        if time.time() - cat.scanned_at > 24 * 3600:
            await asyncio.to_thread(resolver.rescan)
        if cfg.default.voice.app_hotwords:
            transcriber.hotwords = ", ".join(resolver.names(cfg.default.voice.app_hotwords))
    except Exception:  # noqa: BLE001 - app names are a help, never a reason to stop
        _log.exception("app catalog scan failed", extra={"action": "apps.scan"})


def build_transcriber(cfg: AppConfig) -> Transcriber:
    wcfg = cfg.models.whisper
    whisper_path = wcfg.path if wcfg.path.is_absolute() else cfg.root / wcfg.path
    return Transcriber(whisper_path, wcfg.device, wcfg.compute_type,
                       cfg.resources.idle_unload_seconds.whisper,
                       cfg.default.voice.beam_size,
                       hint_words=cfg.default.voice.hint_words,
                       vad_speech_pad_ms=cfg.default.voice.vad_speech_pad_ms)


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
              if a.health.state in USABLE and router.capabilities.routable(n)
              and router.breaker.open_for(n) == 0]
    parts = []
    for name, a in router.adapters.items():
        short = name.split("_")[0] if name != "ollama_local" else "ollama"
        left = router.breaker.open_for(name)       # plan §8.6: skipped while open
        state = (f"circuit-open {int(left // 60) + 1}m" if left > 0
                 else a.health.state.value.lower())
        parts.append(f"{short}={state}")
    return bool(usable), " · ".join(parts)


async def _provider_health_loop(providers: ProviderRegistry, stop: asyncio.Event,
                                router: ProviderRouter | None = None,
                                every: float = 60.0) -> None:
    while not stop.is_set():
        try:
            results = await providers.check_all()
            if router is not None:
                for name, health in results.items():
                    router.note_health(name, health.state, health.detail)
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
            text: str, external_id: str | None, task_id: int | None = None) -> None:
    if store is None:
        return
    try:
        store.record_message(channel=channel, chat_id=chat_id, direction=direction,
                             text=redact(text), external_id=external_id, task_id=task_id)
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
        watch_stop_file(_request_stop)          # Phase 23: the supervisor's graceful stop
        exiting = ExitRequest()
        try:
            await run_core_service(ctx, stop, api, whitelist, exit_request=exiting)
        except TelegramAuthError:
            return EXIT_TELEGRAM_AUTH
        except TelegramError as e:
            # not a sign-in problem: an ordinary failure, restarted quickly
            _log.error(f"telegram startup failed: {e}", extra={"action": "service.start",
                                                              "status": "error"})
            return 1
        return exiting.code

    try:
        return asyncio.run(_amain())
    finally:
        shutdown_logging()
