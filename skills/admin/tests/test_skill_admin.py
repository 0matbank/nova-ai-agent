"""Phase 22: the admin skill end-to-end through the real permission stack and the real
broker request handling (in-process, fake Windows backend — nothing on the PC changes).

The pass condition is "approved elevated actions securely work": an approved action
runs exactly once with exactly the approved parameters; anything unapproved, replayed,
tampered with, not allowlisted or during /lockdown is refused by the broker itself."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.broker import client as broker_client
from core.queue.engine import ApprovalPending
from core.skills.api import PolicyDenied
from tests.mocks.broker import FakeBackend, InProcessClient, make_broker
from tests.mocks.skills import SkillEnv, make_skill_env


@pytest.fixture
def env(tmp_path: Path) -> Iterator[tuple[SkillEnv, FakeBackend, InProcessClient]]:
    s = make_skill_env(tmp_path)
    broker, backend, token = make_broker(tmp_path, tmp_path / "db" / "agent.db")
    client = InProcessClient(broker, token)
    broker_client.set_factory(lambda cfg: client)
    yield s, backend, client
    broker_client.set_factory(None)


def test_status_is_green_and_shows_the_allowlist(env) -> None:  # type: ignore[no-untyped-def]
    s, _, _ = env
    r = s.call("admin", "status", {})
    assert r.ok and "Spooler" in r.summary and "flush_dns" in r.summary
    assert s.tasks.buttons == []                               # no approval asked


def test_approved_service_restart_runs_once_with_proof(env) -> None:  # type: ignore[no-untyped-def]
    s, be, _ = env
    q = s.call("admin", "service", {"name": "Spooler", "op": "query"})
    assert q.ok and "running" in q.summary and s.tasks.buttons == []
    r, _ = s.call_with_approval("admin", "service", {"name": "Spooler", "op": "restart"})
    assert r is not None and r.ok
    assert be.controls == [("Spooler", "restart")]
    assert r.evidence["broker_verified"] and r.evidence["approval_id"] and \
        r.evidence["state"] == "running"


def test_rejected_means_nothing_runs(env) -> None:  # type: ignore[no-untyped-def]
    s, be, _ = env
    r, _ = s.call_with_approval("admin", "service", {"name": "Spooler", "op": "stop"},
                                approve=False)
    assert r is None and be.controls == []


def test_service_outside_the_allowlist_is_refused_by_the_broker(env) -> None:  # type: ignore[no-untyped-def]
    s, be, _ = env
    r, _ = s.call_with_approval("admin", "service", {"name": "WinDefend", "op": "stop"})
    assert r is not None and not r.ok and "allowlist" in r.summary
    assert be.controls == []


def test_broker_refuses_without_approval_replay_and_tampering(env) -> None:  # type: ignore[no-untyped-def]
    s, be, client = env
    params = {"name": "Spooler", "op": "restart"}
    # 1. core bypassed entirely: no approval exists
    reply = client.raw("service", params, "service Spooler", task_id=1)
    assert reply["error"] == "APPROVAL_REQUIRED" and be.controls == []
    # 2. a real approval, used once …
    r, tid = s.call_with_approval("admin", "service", params)
    assert r is not None and r.ok and len(be.controls) == 1
    # … can never be used again (the broker's own single-use ledger)
    reply = client.raw("service", params, "service Spooler", task_id=tid)
    assert reply["error"] == "APPROVAL_REQUIRED" and "already used" in reply["detail"]
    # 3. the same task's approval does not cover different parameters
    reply = client.raw("service", {"name": "W32Time", "op": "restart"}, "service Spooler",
                       task_id=tid)
    assert reply["error"] == "APPROVAL_REQUIRED" and len(be.controls) == 1


def test_an_old_approval_is_refused(env, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    s, be, client = env
    tid = s.new_task()
    with pytest.raises(ApprovalPending):
        s.call("admin", "task", {"name": "flush_dns"}, tid)
    nonce = s.tasks.last_callback(True).split(":")[1]
    s.tasks.approvals.decide(nonce, True, "555")
    # the owner approved, but long ago (core's own expiry is longer than the broker's)
    old =(datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None)
    con = sqlite3.connect(tmp_path / "db" / "agent.db")
    con.execute("UPDATE approvals SET decided_at = ? WHERE task_id = ?", (old.isoformat(" "),
                                                                          tid))
    con.commit()
    con.close()
    r = s.call("admin", "task", {"name": "flush_dns"}, tid)
    assert not r.ok and "too old" in r.summary and be.calls == []


def test_firewall_rule_add_list_remove(env) -> None:  # type: ignore[no-untyped-def]
    s, be, _ = env
    add = {"op": "add", "name": "Block 8080", "direction": "in", "rule_action": "block",
           "protocol": "TCP", "port": "8080"}
    r, _ = s.call_with_approval("admin", "firewall", add)
    assert r is not None and r.ok and r.evidence["rule"] == "Nova AI - Block 8080"
    netsh = next(c for c in be.calls if c[0].endswith("netsh.exe"))
    assert "name=Nova AI - Block 8080" in netsh and "localport=8080" in netsh
    lst = s.call("admin", "firewall", {"op": "list"})
    assert lst.ok and lst.data["rules"][0]["name"] == "Nova AI - Block 8080"
    r, _ = s.call_with_approval("admin", "firewall", {**add, "op": "remove"})
    assert r is not None and r.ok and be.rules == []


def test_restart_is_delayed_and_cancel_needs_no_approval(env) -> None:  # type: ignore[no-untyped-def]
    s, be, _ = env
    r, _ = s.call_with_approval("admin", "power", {"op": "restart", "delay_seconds": 120})
    assert r is not None and r.ok
    argv = be.calls[-1]
    assert argv[0].lower().endswith("shutdown.exe") and argv[1:4] == ["/r", "/t", "120"]
    c = s.call("admin", "power", {"op": "cancel"})
    assert c.ok and be.calls[-1][1] == "/a" and len(s.tasks.buttons) == 1  # only the restart


def test_package_install_shows_the_package_and_verifies(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    s, be, _ = env
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)   # no lookup in tests
    r, _ = s.call_with_approval("admin", "package", {"op": "install", "id": "7zip.7zip"})
    assert r is not None and r.ok and r.evidence["installed"] is True
    install = next(c for c in be.calls if c[1] == "install")
    assert install[install.index("--id") + 1] == "7zip.7zip" and "--exact" in install
    assert "--source" in install and install[install.index("--source") + 1] == "winget"


def test_unknown_task_is_refused_before_asking(env) -> None:  # type: ignore[no-untyped-def]
    s, _, _ = env
    with pytest.raises(PolicyDenied):
        s.call("admin", "task", {"name": "format_disk"})
    assert s.tasks.buttons == []


def test_lockdown_stops_admin_work_but_not_cancel(env) -> None:  # type: ignore[no-untyped-def]
    s, be, client = env
    client.raw("lockdown", {"on": True})
    r, _ = s.call_with_approval("admin", "task", {"name": "flush_dns"})
    assert r is not None and not r.ok and "lockdown" in r.summary and be.calls == []
    assert s.call("admin", "power", {"op": "cancel"}).ok
    client.raw("lockdown", {"on": False})
    r, _ = s.call_with_approval("admin", "task", {"name": "flush_dns"})
    assert r is not None and r.ok


def test_not_elevated_broker_says_so(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path)
    broker, be, token = make_broker(tmp_path, tmp_path / "db" / "agent.db", elevated=False)
    broker_client.set_factory(lambda cfg: InProcessClient(broker, token))
    try:
        r, _ = s.call_with_approval("admin", "task", {"name": "flush_dns"})
    finally:
        broker_client.set_factory(None)
    assert r is not None and not r.ok and "administrator" in r.summary and be.calls == []
