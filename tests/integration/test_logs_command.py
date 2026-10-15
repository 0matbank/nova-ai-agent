"""/logs (plan §27, §42 "Activity logs") — recent audit entries on the phone."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from channels.base import IncomingMessage
from core.orchestrator.log_commands import LogCommands
from tests.mocks.tasks import make_task_env


def msg(text: str) -> IncomingMessage:
    return IncomingMessage(channel="telegram", chat_id="555", user_id="555", message_id="1",
                           text=text, received_at=datetime.now(UTC))


def test_logs_shows_newest_first_and_respects_the_count(tmp_path: Path) -> None:
    env = make_task_env(tmp_path / "agent.db", tmp_path / "bk")
    for i in range(20):
        env.audit.record(actor="skill:files", action="file.delete_workspace",
                         status="ok", target=f"C:/ws/f{i}.txt")
    env.audit.record(actor="system", action="approval.request", status="pending",
                     target="C:/Users/x/Documents")
    cmd = LogCommands(env.store._sessions, "Asia/Dhaka")
    out = asyncio.run(cmd.logs(msg("/logs"), []))
    lines = out.splitlines()
    assert "শেষ 15টা" in lines[0] and len(lines) == 16
    assert "approval.request [pending]" in lines[1] and "f19.txt" in lines[2]
    assert len(asyncio.run(cmd.logs(msg("/logs 3"), ["3"])).splitlines()) == 4
    assert len(asyncio.run(cmd.logs(msg("/logs 999"), ["999"])).splitlines()) == 22  # all 21


def test_logs_empty(tmp_path: Path) -> None:
    env = make_task_env(tmp_path / "agent.db", tmp_path / "bk")
    out = asyncio.run(LogCommands(env.store._sessions, "Asia/Dhaka").logs(msg("/logs"), []))
    assert "কোনো activity log নেই" in out
