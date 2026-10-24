"""V1.2: "শুনেছি — এখন X করছি" the moment a request arrives — from the fixed rules,
no AI, and nothing claimed for a request the rules do not understand."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from channels.base import IncomingMessage
from core.router.ack import ack_for


@pytest.mark.parametrize(("text", "want"), [
    ("volume 30 koro", "🔊 volume 30% করছি"),
    ("আওয়াজ কমাও", "🔊 volume কমাচ্ছি"),
    ("mute koro", "🔇 mute করছি"),
    ("পরের গান চালাও", "⏭ পরেরটা চালাচ্ছি"),
    ("task manager dekhao", "📋 কী কী চলছে দেখছি"),
    ("PC te ki cholche bolo", None),                # the AI decides this one: no claim
    ("screenshot dao", "📸 screenshot নিচ্ছি"),
    ("aaj dhaka r weather kemon", "🔎 খুঁজে দেখছি"),
    ("kemon acho?", None),                          # a chat: nothing to claim
])
def test_what_nova_says_it_will_do(text: str, want: str | None) -> None:
    assert ack_for(text) == want


def test_the_receipt_starts_with_what_happens_now(tmp_path: Path) -> None:
    from core.orchestrator.task_commands import TaskCommands
    from tests.mocks.tasks import make_task_env
    env = make_task_env(tmp_path / "t.db", tmp_path / "b")
    tc = TaskCommands(env.store, env.engine, 10, "Asia/Dhaka", describe=ack_for)
    msg = IncomingMessage("telegram", "555", "555", "1", "volume 30 koro", datetime.now(UTC))
    out = asyncio.run(tc.submit(msg))
    lines = out.text.splitlines()
    assert lines[0] == "👂 শুনেছি — এখন 🔊 volume 30% করছি"
    assert lines[1].startswith("📥 Task #")
    plain = asyncio.run(tc.submit(IncomingMessage("telegram", "555", "555", "2",
                                                  "kemon acho?", datetime.now(UTC))))
    assert plain.text.startswith("📥 Task #")                # no claim when unclear
