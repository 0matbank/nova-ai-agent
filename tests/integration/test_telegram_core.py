"""Phase 2: Telegram + authentication + /status, against a fake Bot API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from channels.base import OutgoingMessage
from channels.telegram.alerts import TelegramAlertHandler
from channels.telegram.api import TelegramAuthError, chunk_text
from channels.telegram.auth import ChatWhitelist
from channels.telegram.channel import ERROR_REPLY, UNSUPPORTED_REPLY, TelegramChannel
from core.bootstrap import bootstrap
from core.config import ConfigError, Secrets
from core.log import get_logger, setup_logging, shutdown_logging
from core.log.setup import ROOT_LOGGER
from core.orchestrator.commands import CommandRouter, HealthSources
from core.service import run_core_service, telegram_settings
from tests.mocks.tasks import make_task_env
from tests.mocks.telegram import OWNER, TOKEN, FakeTelegram

CATS = ["core", "desktop", "browser", "provider", "tasks", "audit"]
STRANGER = 999888777


@pytest.fixture
def logs(tmp_path: Path) -> Iterator[Path]:
    d = tmp_path / "logs"
    setup_logging(d, CATS, worker="core", known_secrets=[TOKEN], console=False)
    yield d
    shutdown_logging()


def _read(logs: Path, cat: str) -> list[dict]:
    for h in logging.getLogger(f"{ROOT_LOGGER}.{cat}").handlers:
        h.flush()
    p = logs / cat / f"{cat}.log"
    return [json.loads(x) for x in p.read_text("utf-8").splitlines()] if p.exists() else []


def _channel(fake: FakeTelegram, tmp_path: Path, probes=None) -> TelegramChannel:
    env = make_task_env(tmp_path / "tg.db", tmp_path / "bk")
    router = CommandRouter(tmp_path, HealthSources(probes or {}), "Test Agent", env.commands)
    return TelegramChannel(fake.api(), ChatWhitelist([str(OWNER)]), router.handle,
                           poll_timeout=1)


def _poll(ch: TelegramChannel) -> int:
    return asyncio.run(ch.poll_once())


# ------------------------------------------------------------ authorization

def test_authorized_status_works(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.push_message("/status")
    ch = _channel(fake, tmp_path, {"Telegram": lambda: (True, "connected")})
    assert _poll(ch) == 1
    [reply] = fake.texts_to(OWNER)
    assert "online" in reply and "Health: OK" in reply
    assert "CPU:" in reply and "RAM:" in reply and "Disk free:" in reply


def test_unknown_user_ignored_and_audited(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.push_message("/status", chat_id=STRANGER)
    _poll(_channel(fake, tmp_path))
    assert fake.sent == []
    [entry] = _read(logs, "audit")
    assert entry["action"] == "auth.reject" and str(STRANGER) in entry["error_code"]


def test_group_chat_with_whitelisted_id_ignored(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.push_message("/status", chat_id=OWNER, chat_type="group")
    _poll(_channel(fake, tmp_path))
    assert fake.sent == []


def test_spoofed_sender_in_owner_chat_ignored(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.push_message("/status", chat_id=OWNER, user_id=STRANGER)
    _poll(_channel(fake, tmp_path))
    assert fake.sent == []


def test_whitelist_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        ChatWhitelist(["12345", "abc"])
    assert len(ChatWhitelist.from_csv(" 1, 2 ,,")) == 2


# ----------------------------------------------------------------- commands

@pytest.mark.parametrize("text, expected", [
    ("kemon acho?", "📥 Task #1 তৈরি হয়েছে"),
    ("PC te ki cholche bolo", "📥 Task #1 তৈরি হয়েছে"),
    ("/help", "চালু: /status /tasks /task /cancel /pause /resume /pc /help"),
    ("/tasks", "এখনো কোনো task নেই"),
    ("/screenshot", "এখনো চালু হয়নি"),
    ("/foo", "অজানা command"),
    ("/staus", "আপনি কি /status বোঝাতে চেয়েছেন?"),
    ("/Status", "online"),
    ("/pc", "PC status"),
    ("/status@test_agent_bot", "online"),
])
def test_command_replies(text: str, expected: str, logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.push_message(text)
    _poll(_channel(fake, tmp_path))
    [reply] = fake.texts_to(OWNER)
    assert expected in reply


def test_non_text_message(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.push_message(None, extra={"voice": {"file_id": "x", "duration": 2}})
    _poll(_channel(fake, tmp_path))
    assert fake.texts_to(OWNER) == [UNSUPPORTED_REPLY]


def test_failing_probe_reports_degraded(logs: Path, tmp_path: Path) -> None:
    def boom() -> tuple[bool, str]:
        raise RuntimeError("x")
    fake = FakeTelegram()
    fake.push_message("/status")
    _poll(_channel(fake, tmp_path, {"Broken": boom}))
    [reply] = fake.texts_to(OWNER)
    assert "Health: DEGRADED" in reply and "probe error" in reply


def test_handler_exception_gives_error_reply(logs: Path, tmp_path: Path) -> None:
    async def bad(msg):  # type: ignore[no-untyped-def]
        raise RuntimeError(f"kaboom {TOKEN}")
    fake = FakeTelegram()
    fake.push_message("hello")
    ch = TelegramChannel(fake.api(), ChatWhitelist([str(OWNER)]), bad, poll_timeout=1)
    _poll(ch)
    assert fake.texts_to(OWNER) == [ERROR_REPLY]
    text = (logs / "core" / "core.log").read_text("utf-8")
    assert "kaboom" in text and TOKEN not in text


# ------------------------------------------------------------ polling logic

def test_updates_processed_once(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.push_message("/help")
    ch = _channel(fake, tmp_path)
    _poll(ch)
    _poll(ch)
    assert len(fake.texts_to(OWNER)) == 1
    assert ch.offset == 2


def _run_until(ch: TelegramChannel, cond, timeout: float = 5.0) -> None:
    async def go() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(ch.run(stop))
        deadline = asyncio.get_running_loop().time() + timeout
        while not cond():
            if task.done():
                task.result()
            assert asyncio.get_running_loop().time() < deadline, "condition not reached"
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, 5)
    asyncio.run(go())


def test_rate_limit_then_recover(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.error("getUpdates", 429, "Too Many Requests", retry_after=0)
    fake.push_message("/help")
    ch = _channel(fake, tmp_path)
    _run_until(ch, lambda: len(fake.sent) == 1)


def test_network_errors_back_off_and_recover(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.script["getUpdates"].append(httpx.ConnectError("down"))
    fake.error("getUpdates", 502, "Bad Gateway")
    fake.push_message("/help")
    ch = _channel(fake, tmp_path)
    _run_until(ch, lambda: len(fake.sent) == 1, timeout=10)
    warnings = [e for e in _read(logs, "core") if e.get("status") == "retrying"]
    assert len(warnings) == 2


def test_invalid_token_is_fatal(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    router = CommandRouter(tmp_path, HealthSources(), "x")
    ch = TelegramChannel(fake.api("111111111:" + "W" * 35), ChatWhitelist([str(OWNER)]),
                         router.handle, poll_timeout=1)
    with pytest.raises(TelegramAuthError):
        asyncio.run(ch.run(asyncio.Event()))
    crit = [e for e in _read(logs, "core") if e["level"] == "CRITICAL"]
    assert crit and crit[0]["error_code"] == "AUTH_REQUIRED"


def test_stop_interrupts_long_poll_and_acks(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.push_message("/help")
    ch = _channel(fake, tmp_path)
    _poll(ch)
    fake.script["getUpdates"].append("hang")

    async def go() -> float:
        stop = asyncio.Event()
        task = asyncio.create_task(ch.run(stop))
        await asyncio.sleep(0.2)
        t0 = asyncio.get_running_loop().time()
        stop.set()
        await asyncio.wait_for(task, 3)
        return asyncio.get_running_loop().time() - t0

    assert asyncio.run(go()) < 2
    method, params = fake.calls[-1]
    assert method == "getUpdates" and params["offset"] == 2 and params["timeout"] == 0


def test_long_reply_chunked() -> None:
    text = "\n".join(f"line {i} " + "x" * 90 for i in range(200))
    parts = chunk_text(text)
    assert len(parts) > 1 and all(len(p) <= 4096 for p in parts)
    assert "".join(parts) == text
    assert all(len(p) <= 4096 for p in chunk_text("y" * 10000))


def test_token_never_logged_on_errors(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    fake.script["getUpdates"].append(httpx.ConnectError(f"fail https://x/bot{TOKEN}/getUpdates"))
    fake.push_message("/help")
    _run_until(_channel(fake, tmp_path), lambda: len(fake.sent) == 1, timeout=10)
    shutdown_logging()
    for f in logs.rglob("*.log"):
        assert TOKEN not in f.read_text("utf-8")


# ------------------------------------------------------------ alerts/service

def test_critical_log_alerts_owner(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    ch = _channel(fake, tmp_path)

    async def go() -> None:
        handler = TelegramAlertHandler(ch, asyncio.get_running_loop())
        root = logging.getLogger(ROOT_LOGGER)
        root.addHandler(handler)
        try:
            get_logger("core").critical("database corruption detected")
            for _ in range(100):
                if fake.sent:
                    break
                await asyncio.sleep(0.01)
        finally:
            root.removeHandler(handler)
    asyncio.run(go())
    [alert] = fake.texts_to(OWNER)
    assert "CRITICAL" in alert and "database corruption" in alert


def test_service_refuses_without_secrets(config_dir: Path, runtime_root: Path) -> None:
    ctx = bootstrap(config_dir=config_dir, root=runtime_root, console_logs=False)
    shutdown_logging()
    with pytest.raises(ConfigError) as ei:
        telegram_settings(ctx)
    assert any("TELEGRAM_BOT_TOKEN" in e for e in ei.value.errors)
    assert any("TELEGRAM_ALLOWED_CHAT_IDS" in e for e in ei.value.errors)

    bad = type(ctx)(ctx.config, Secrets({"TELEGRAM_BOT_TOKEN": SecretStr(TOKEN),
                                         "TELEGRAM_ALLOWED_CHAT_IDS": SecretStr("me")}))
    with pytest.raises(ConfigError):
        telegram_settings(bad)


def test_service_end_to_end(config_dir: Path, runtime_root: Path) -> None:
    ctx = bootstrap(config_dir=config_dir, root=runtime_root, console_logs=False)
    fake = FakeTelegram()
    fake.push_message("/status")
    fake.push_message("/status", chat_id=STRANGER)

    async def go() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_core_service(ctx, stop, fake.api(), ChatWhitelist([str(OWNER)])))
        for _ in range(500):
            if fake.sent:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, 5)
    asyncio.run(go())
    shutdown_logging()
    assert fake.calls[0][0] == "getMe"
    assert [c["command"] for c in fake.commands] == [
        "status", "tasks", "task", "cancel", "pause", "resume", "pc", "help"]
    [reply] = fake.texts_to(OWNER)
    assert "✅ Database: ok (schema 001)" in reply
    assert "✅ Telegram: connected" in reply and "✅ Config: valid" in reply
    assert fake.texts_to(STRANGER) == []
    core_log = (runtime_root / "logs/core/core.log").read_text("utf-8")
    assert "core service started" in core_log and "core service stopped" in core_log


def _run_service(ctx, fake: FakeTelegram, until) -> None:  # type: ignore[no-untyped-def]
    async def go() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_core_service(ctx, stop, fake.api(), ChatWhitelist([str(OWNER)])))
        for _ in range(500):
            if until():
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, 5)
    asyncio.run(go())
    shutdown_logging()


def test_service_safe_mode_on_bad_database(config_dir: Path, runtime_root: Path) -> None:
    import sqlite3
    ctx = bootstrap(config_dir=config_dir, root=runtime_root, console_logs=False)
    db = ctx.config.path("database")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
    conn.execute("INSERT INTO alembic_version VALUES ('999_from_future')")
    conn.commit()
    conn.close()

    fake = FakeTelegram()
    fake.push_message("/status")
    fake.push_message("Click TV test koro")
    fake.push_message("/tasks")
    _run_service(ctx, fake, lambda: len(fake.texts_to(OWNER)) >= 4)
    texts = fake.texts_to(OWNER)
    alert = next(t for t in texts if "CRITICAL ALERT" in t)
    assert "SAFE MODE" in alert
    status = next(t for t in texts if "online" in t)
    assert "Health: SAFE MODE" in status and "❌ Database: SAFE MODE" in status
    assert sum("⚠️ SAFE MODE" in t for t in texts) == 2   # text + /tasks refused


def test_messages_recorded_and_redacted(config_dir: Path, runtime_root: Path) -> None:
    from sqlalchemy import select

    from core.db.engine import make_engine, make_sessionmaker
    from core.db.models import Message

    ctx = bootstrap(config_dir=config_dir, root=runtime_root, console_logs=False)
    fake = FakeTelegram()
    fake.push_message(f"amar token {TOKEN} save koro")
    _run_service(ctx, fake, lambda: bool(fake.sent))
    engine = make_engine(ctx.config.path("database"))
    with make_sessionmaker(engine)() as s:
        rows = list(s.scalars(select(Message).order_by(Message.id)))
    engine.dispose()
    assert [r.direction for r in rows] == ["in", "out"]
    assert TOKEN not in rows[0].text and "REDACTED" in rows[0].text
    assert "Task #1" in rows[1].text


def test_outgoing_buttons_rendered(logs: Path, tmp_path: Path) -> None:
    fake = FakeTelegram()
    ch = _channel(fake, tmp_path)
    asyncio.run(ch.send(str(OWNER), OutgoingMessage("x", [[("Approve", "a:1")]])))
    assert fake.sent[0]["reply_markup"] == {
        "inline_keyboard": [[{"text": "Approve", "callback_data": "a:1"}]]}
