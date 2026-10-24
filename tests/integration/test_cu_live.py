"""Plan §72 CU-7 — live execution view: one Telegram message per task, edited in place
with the real steps; a timeline of real events for the dashboard (token-protected);
fast updates folded so Telegram is not flooded — never invented progress."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from channels.base import OutgoingMessage
from core.notify import notifier as nmod
from core.notify.events import TaskEvents
from core.notify.notifier import MessageType, Notifier


def _notifier(sent: list[OutgoingMessage]) -> Notifier:
    from core.config import load_config

    async def send(chat: str, msg: OutgoingMessage) -> None:
        sent.append(msg)
    return Notifier(load_config().default.notification_throttle, send)


def test_live_updates_are_one_message_and_folded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nmod, "LIVE_EDIT_SECONDS", 0.2)
    sent: list[OutgoingMessage] = []

    async def go() -> None:
        n = _notifier(sent)
        assert await n.notify("555", MessageType.LIVE, "step 1", task_id=7)
        assert not await n.notify("555", MessageType.LIVE, "step 2", task_id=7)   # folded
        assert not await n.notify("555", MessageType.LIVE, "step 3", task_id=7)
        await asyncio.sleep(0.4)
        await n.wait_hooks()
    asyncio.run(go())
    assert [m.text for m in sent] == ["step 1", "step 3"]          # the newest, not stale
    assert {m.live_key for m in sent} == {"task-7"}


class _Api:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edited: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str, markup: Any = None) -> dict:
        self.sent.append(text)
        return {"message_id": 41}

    async def edit_message_text(self, chat_id: str, message_id: str, text: str) -> None:
        self.edited.append((message_id, text))


def test_telegram_edits_the_live_message_in_place() -> None:
    from channels.telegram.channel import TelegramChannel
    ch = TelegramChannel.__new__(TelegramChannel)
    ch.api = _Api()                                   # type: ignore[assignment]
    from collections import OrderedDict
    ch._live = OrderedDict()

    async def go() -> None:
        await ch.send("555", OutgoingMessage("one", live_key="task-9"))
        await ch.send("555", OutgoingMessage("two", live_key="task-9"))
        await ch.send("555", OutgoingMessage("other task", live_key="task-10"))
    asyncio.run(go())
    assert ch.api.sent == ["one", "other task"]          # type: ignore[attr-defined]
    assert ch.api.edited == [("41", "two")]              # type: ignore[attr-defined]


def test_events_are_ordered_bounded_and_resumable() -> None:
    ev = TaskEvents()
    a = ev.add(1, "observation_ready", "12 controls", window="Export")
    b = ev.add(1, "action_delivered", "click [6] → ok", effect="changed")
    ev.add(2, "progress", "other task")
    assert [e["seq"] for e in ev.since(1)] == [a["seq"], b["seq"]]
    assert ev.since(1, after=a["seq"]) == [b]             # a reconnect sees nothing twice
    with pytest.raises(ValueError):
        ev.add(1, "made_up_progress_95_percent", "x")


def test_dashboard_serves_task_events_only_with_the_token(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from core.dashboard.api import DashboardDeps, create_dashboard_app
    from core.notify.events import EVENTS
    from tests.mocks.skills import make_skill_env
    s = make_skill_env(tmp_path)
    token = "t" * 43
    app = create_dashboard_app(DashboardDeps(cfg=s.cfg, token=token, owner_chat="555",
                                             store=s.tasks.store), 47810,
                               Path(__file__).resolve().parents[2] / "dashboard" / "dist")
    c = TestClient(app, base_url="http://127.0.0.1:47810")
    first = EVENTS.add(4242, "action_delivered", "set_text [9] → ok")
    assert c.get("/api/tasks/4242/events").status_code == 401
    r = c.get("/api/tasks/4242/events", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["events"][-1]["summary"] == "set_text [9] → ok"
    r = c.get(f"/api/tasks/4242/events?after={first['seq']}",
              headers={"Authorization": f"Bearer {token}"})
    assert r.json()["events"] == []
