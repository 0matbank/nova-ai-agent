"""Phase 19 pass — "Future/recurring jobs restart-এর পরেও থাকে": schedules made from a
message live in the DB; a NEW scheduler (= after a restart) runs them at their time,
recurring ones come back, missed ones are handled honestly (plan §39, §33)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.config import load_config
from core.orchestrator.executor import UserRequestExecutor
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from core.scheduler import Scheduler, ScheduleStore
from models.router import ProviderRouter
from providers.provider_base import HealthState
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import SkillEnv, make_skill_env


def setup(tmp_path: Path) -> tuple[SkillEnv, ScheduleStore]:
    s = make_skill_env(tmp_path)
    cfg = load_config()
    ad = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in cfg.providers.providers}
    ad["ollama_local"] = FakeAdapter("ollama_local", [])
    router = ProviderRouter(cfg.providers, ad)
    store = ScheduleStore(s.tasks.store._sessions)
    s.tasks.engine.register("user_request", UserRequestExecutor(
        IntentRouter(router), router, s.runner, schedules=store))
    return s, store


def run_all(s: SkillEnv) -> None:
    for _ in range(6):
        if not asyncio.run(s.tasks.engine.run_once()):
            break


def submit(s: SkillEnv, text: str) -> int:
    tid = int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                   channel="telegram", chat_id="555").id)
    run_all(s)
    return tid


def restart(s: SkillEnv) -> Scheduler:
    """What the service builds on start: a fresh store + scheduler over the same DB."""
    return Scheduler(ScheduleStore(s.tasks.store._sessions), s.tasks.store,
                     s.tasks.engine.notifier, "Asia/Dhaka", wake=s.tasks.engine.wake)


def test_jobs_survive_a_restart_and_run_on_time(tmp_path: Path) -> None:
    s, store = setup(tmp_path)
    t1 = submit(s, "30 minute por pc status bolo")
    t2 = submit(s, "protidin raat 11 tay pc status bolo")
    t3 = submit(s, "2 ghonta por amake mone koriye dio pani khete")
    for tid in (t1, t2, t3):
        t = s.tasks.store.get(tid)
        assert t.state is TaskState.COMPLETED and "Schedule #" in t.result_summary, t
    jobs = {j.what: j for j in store.active()}
    assert set(jobs) == {"pc status bolo", "pani khete"} | {"pc status bolo"}
    assert len(store.active()) == 3
    done = next(m for _, m in s.tasks.sent if f"TASK #{t2} COMPLETE" in m)
    assert "DB-তে আছে ✓" in done and "প্রতিদিন 11:00 PM" in done   # owner sees the time

    sched = restart(s)                                    # ---- the service restarts
    delayed = next(j for j in store.active() if j.kind == "delayed" and j.action == "task")
    rem = next(j for j in store.active() if j.action == "remind")
    daily = next(j for j in store.active() if j.kind == "recurring")   # before any tick
    assert delayed.next_run_at is not None
    # the delayed job and the reminder are not due yet (the 23:00 daily job may be when
    # the test runs between 22:30 and 23:00 — it failed then)
    early = asyncio.run(sched.tick(delayed.next_run_at - timedelta(minutes=1)))
    assert delayed.id not in early and rem.id not in early
    # the delayed job's time comes → it runs as a normal task through the engine
    fired = asyncio.run(sched.tick(delayed.next_run_at + timedelta(seconds=5)))
    assert fired == [delayed.id]
    after = store.get(delayed.id)
    assert after is not None and not after.enabled and after.last_task_id is not None
    run_all(s)
    ran = s.tasks.store.get(after.last_task_id)
    assert ran.state is TaskState.COMPLETED and "CPU:" in ran.result_summary
    # the recurring job runs at 23:00 and comes back for the next day
    assert daily.next_run_at is not None
    # each job is ticked at its own time, in time order (one shared tick made the
    # reminder 22 h "late" when the test ran after 23:00, and other hours broke too)
    assert rem.next_run_at is not None
    for when in sorted({daily.next_run_at, rem.next_run_at}):
        asyncio.run(restart(s).tick(when + timedelta(seconds=5)))
    again = store.get(daily.id)
    assert again is not None and again.enabled and again.run_count == 1
    assert again.next_run_at is not None
    assert again.next_run_at - daily.next_run_at == timedelta(days=1)
    # the reminder (due 2 h later — already passed by 23:00) is a message, not a task
    assert rem.next_run_at is not None
    assert store.get(rem.id).enabled is False                    # type: ignore[union-attr]
    assert any("মনে করিয়ে দিচ্ছি: pani khete" in m for _, m in s.tasks.sent)


def test_missed_jobs_while_nova_was_off(tmp_path: Path) -> None:
    s, store = setup(tmp_path)
    now = datetime.now(UTC)
    late = store.add(kind="once", action="remind", what="call mom", next_run_at=now -
                     timedelta(minutes=30), channel="telegram", chat_id="555")
    old = store.add(kind="once", action="task", what="pc status bolo",
                    next_run_at=now - timedelta(days=2), channel="telegram", chat_id="555")
    daily = store.add(kind="recurring", action="task", what="pc status bolo",
                      next_run_at=now - timedelta(days=3), channel="telegram", chat_id="555",
                      rule={"every": "day", "at": "09:00"})
    asyncio.run(restart(s).tick(now))
    msgs = [m for _, m in s.tasks.sent]
    assert any("call mom" in m and "দেরিতে" in m for m in msgs)       # 30 min late: runs
    assert any(f"Schedule #{old.id}" in m and "চালাইনি" in m for m in msgs)  # 2 days: not run
    assert store.get(old.id).enabled is False                              # type: ignore[union-attr]
    d = store.get(daily.id)
    assert d is not None and d.enabled and d.next_run_at is not None and d.next_run_at > now
    assert store.get(late.id).enabled is False                             # type: ignore[union-attr]


def test_condition_jobs(tmp_path: Path) -> None:
    s, store = setup(tmp_path)
    watched = s.tasks.store.create(title="x", request_text="x", task_type="nothing",
                                   channel="telegram", chat_id="555")
    t = submit(s, f"task #{watched.id} shesh hole amake janao")
    assert "Schedule #" in s.tasks.store.get(t).result_summary
    target = tmp_path / "incoming.pdf"
    submit(s, f"{target} file ashle amake bolo")
    sched = restart(s)
    assert asyncio.run(sched.tick()) == []                       # neither has happened
    s.tasks.store.transition(watched.id, TaskState.CANCELLED)
    target.write_text("x", encoding="utf-8")
    assert len(asyncio.run(sched.tick())) == 2
    msgs = [m for _, m in s.tasks.sent]
    assert any(f"Task #{watched.id} শেষ হয়েছে" in m for m in msgs)
    assert any("incoming.pdf এসেছে" in m for m in msgs)
    assert store.active() == []                                  # one-shot: done


def test_unclear_time_asks_and_cancel_works(tmp_path: Path) -> None:
    s, store = setup(tmp_path)
    tid = submit(s, "amake mone koriye dio")
    assert "কখন করব বুঝতে পারিনি" in s.tasks.store.get(tid).result_summary
    from datetime import UTC as _UTC

    from channels.base import IncomingMessage
    from core.orchestrator.schedule_commands import ScheduleCommands
    submit(s, "protidin shokal 9 tay pc status bolo")
    (job,) = store.active()
    cmds = ScheduleCommands(store, "Asia/Dhaka")
    msg = IncomingMessage("telegram", "555", "1", "9", "/schedules", datetime.now(_UTC))
    listing = asyncio.run(cmds.schedules(msg, []))
    assert f"#{job.id} প্রতিদিন 09:00 AM" in listing
    assert "বাতিল করলাম" in asyncio.run(cmds.schedules(msg, ["cancel", str(job.id)]))
    assert store.active() == []


def test_static_jobs_from_config_are_kept_and_updated(tmp_path: Path) -> None:
    s, store = setup(tmp_path)
    sched = restart(s)
    ids = sched.sync_static([{"name": "health", "when": "protidin shokal 9 tay",
                              "do": "website health check koro"}], "555")
    again = restart(s).sync_static([{"name": "health", "when": "protidin shokal 10 tay",
                                     "do": "website health check koro"}], "555")
    assert ids == again and len(store.active()) == 1
    (job,) = store.active()
    assert job.rule == {"every": "day", "at": "10:00"} and job.source == "config:health"
