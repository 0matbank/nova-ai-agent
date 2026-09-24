"""Phase 3: persistent task engine — IDs, state machine, cancel, pause/resume,
restart recovery from checkpoints, retries, history, Telegram task commands."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from channels.base import IncomingMessage
from core.queue.engine import TaskContext, Verdict
from core.queue.states import InvalidTransition, TaskState
from core.queue.store import CancelOutcome, StepStatus
from tests.mocks.tasks import TaskEnv, make_task_env

Hook = Callable[[int, TaskContext], Awaitable[None]]


class StepsExecutor:
    """Deterministic 3-step executor that records which steps really ran."""

    def __init__(self, steps: int = 3, verify_fails: int = 0, raise_at: int | None = None,
                 hook: Hook | None = None) -> None:
        self.steps = steps
        self.verify_fails = verify_fails
        self.raise_at = raise_at
        self.hook = hook
        self.ran: list[int] = []
        self.plans = 0

    async def plan(self, ctx: TaskContext) -> list[str]:
        self.plans += 1
        return [f"step {i}" for i in range(1, self.steps + 1)]

    async def run(self, ctx: TaskContext) -> str:
        for seq in range(1, self.steps + 1):
            async def fn(prev, seq=seq):  # type: ignore[no-untyped-def]
                if seq > 1:
                    assert prev == {"seq": seq - 1}      # previous checkpoint handed over
                if seq == self.raise_at:
                    raise RuntimeError(f"step {seq} broke")
                self.ran.append(seq)
                if self.hook:
                    await self.hook(seq, ctx)
                return {"seq": seq}
            await ctx.run_step(seq, fn)
        return f"{self.steps} steps done"

    async def verify(self, ctx: TaskContext, result: str) -> Verdict:
        if self.verify_fails > 0:
            self.verify_fails -= 1
            return Verdict(False, "output missing")
        return Verdict(True, "all checks passed")


def new(env: TaskEnv, text: str = "do the thing", ttype: str = "demo", prio: int = 0):  # type: ignore[no-untyped-def]
    return env.store.create(title=text, request_text=text, task_type=ttype,
                            channel="telegram", chat_id="555", priority=prio)


def once(env: TaskEnv) -> bool:
    return asyncio.run(env.engine.run_once())


def msg(text: str) -> IncomingMessage:
    return IncomingMessage("telegram", "555", "555", "1", text, dt.datetime.now(dt.UTC))


def cmd(env: TaskEnv, name: str, *args: str) -> str:
    return asyncio.run(getattr(env.commands, name)(msg(f"/{name}"), list(args)))


# ------------------------------------------------------------------ basics

def test_ids_and_persistence_across_restart(tmp_path: Path) -> None:
    db, bk = tmp_path / "a.db", tmp_path / "bk"
    env = make_task_env(db, bk)
    ids = [new(env, f"t{i}").id for i in range(3)]
    assert ids == [1, 2, 3]
    env2 = make_task_env(db, bk)              # "restart": new engine, same file
    assert [t.id for t in env2.store.list_recent()] == [3, 2, 1]
    assert new(env2).id == 4


def test_invalid_transition_rejected(task_env: TaskEnv) -> None:
    t = new(task_env)
    with pytest.raises(InvalidTransition):
        task_env.store.transition(t.id, TaskState.COMPLETED)
    assert task_env.store.get(t.id).state is TaskState.RECEIVED


def test_full_run_completes_only_after_verification(task_env: TaskEnv) -> None:
    ex = StepsExecutor()
    task_env.engine.register("demo", ex)
    t = new(task_env)
    assert once(task_env)
    done = task_env.store.get(t.id)
    assert done.state is TaskState.COMPLETED and done.result_summary == "3 steps done"
    assert [s.status for s in done.steps] == [StepStatus.DONE] * 3
    assert ex.ran == [1, 2, 3] and done.started_at and done.finished_at
    assert any("TASK #1 COMPLETE" in text for _, text in task_env.sent)


def test_priority_then_fifo(task_env: TaskEnv) -> None:
    task_env.engine.register("demo", StepsExecutor(steps=1))
    a, b, c = new(task_env, "a"), new(task_env, "b", prio=5), new(task_env, "c")
    order = []
    for _ in range(3):
        nxt = task_env.store.next_runnable(["demo"])
        order.append(nxt.id)
        once(task_env)
    assert order == [b.id, a.id, c.id]


def test_task_without_executor_stays_queued(task_env: TaskEnv) -> None:
    t = new(task_env, ttype="user_request")
    assert not once(task_env)
    assert task_env.store.get(t.id).state is TaskState.RECEIVED


# ------------------------------------------------------------------ cancel

def test_cancel_queued_task_immediately(task_env: TaskEnv) -> None:
    t = new(task_env)
    assert task_env.store.request_cancel(t.id) is CancelOutcome.CANCELLED
    assert task_env.store.get(t.id).state is TaskState.CANCELLED
    assert task_env.store.request_cancel(t.id) is CancelOutcome.ALREADY_FINISHED
    assert task_env.store.request_cancel(999) is CancelOutcome.NOT_FOUND


def test_cancel_running_task_stops_at_next_checkpoint(task_env: TaskEnv) -> None:
    async def cancel_during_step2(seq: int, ctx: TaskContext) -> None:
        if seq == 2:
            assert task_env.store.request_cancel(ctx.task.id) is CancelOutcome.REQUESTED
    ex = StepsExecutor(hook=cancel_during_step2)
    task_env.engine.register("demo", ex)
    t = new(task_env)
    once(task_env)
    final = task_env.store.get(t.id)
    assert final.state is TaskState.CANCELLED
    assert ex.ran == [1, 2]                     # step 2 finished cleanly, step 3 never ran
    assert final.steps[1].status is StepStatus.DONE
    assert final.steps[2].status is StepStatus.PENDING


# ------------------------------------------------------------ pause/resume

def test_pause_resume_continues_from_checkpoint(task_env: TaskEnv) -> None:
    async def pause_during_step1(seq: int, ctx: TaskContext) -> None:
        if seq == 1:
            assert task_env.store.pause_queue() == [ctx.task.id]
    ex = StepsExecutor(hook=pause_during_step1)
    task_env.engine.register("demo", ex)
    t = new(task_env)
    once(task_env)
    paused = task_env.store.get(t.id)
    assert paused.state is TaskState.PAUSED and paused.paused_from is TaskState.RUNNING

    other = new(task_env, "second")
    assert not once(task_env)                   # queue paused: nothing dispatched
    assert task_env.store.get(other.id).state is TaskState.RECEIVED

    ex.hook = None
    assert task_env.store.resume_queue() == [t.id]
    assert task_env.store.get(t.id).state is TaskState.RETRYING
    once(task_env)
    assert task_env.store.get(t.id).state is TaskState.COMPLETED
    assert ex.ran == [1, 2, 3]                  # step 1 was NOT run twice
    assert ex.plans == 1                        # plan kept, not rebuilt
    once(task_env)
    assert task_env.store.get(other.id).state is TaskState.COMPLETED


def test_queue_pause_persists_across_restart(tmp_path: Path) -> None:
    env = make_task_env(tmp_path / "a.db", tmp_path / "bk")
    env.store.pause_queue()
    env2 = make_task_env(tmp_path / "a.db", tmp_path / "bk")
    assert env2.store.is_queue_paused()


# ---------------------------------------------------------------- recovery

def _crash_mid_step2(env: TaskEnv) -> int:
    """Leave a task exactly as a killed process would: RUNNING, step1 DONE, step2 CURRENT."""
    t = new(env)
    env.store.transition(t.id, TaskState.PLANNING)
    env.store.ensure_plan(t.id, ["step 1", "step 2", "step 3"])
    env.store.transition(t.id, TaskState.RUNNING)
    env.store.start_step(t.id, 1)
    env.store.complete_step(t.id, 1, {"seq": 1})
    env.store.start_step(t.id, 2)
    return t.id


def test_restart_recovery_resumes_from_last_checkpoint(tmp_path: Path) -> None:
    db, bk = tmp_path / "a.db", tmp_path / "bk"
    tid = _crash_mid_step2(make_task_env(db, bk))

    env = make_task_env(db, bk)                 # process restarted
    ex = StepsExecutor()
    env.engine.register("demo", ex)
    [rec] = env.store.recover_after_restart()
    assert rec.id == tid and rec.state is TaskState.RETRYING
    assert rec.error_code == "INTERRUPTED_BY_RESTART"
    assert rec.steps[1].status is StepStatus.PENDING
    once(env)
    final = env.store.get(tid)
    assert final.state is TaskState.COMPLETED
    assert ex.ran == [2, 3]                     # step 1 not repeated
    assert ex.plans == 0


def test_recovery_honours_pending_cancel(tmp_path: Path) -> None:
    db, bk = tmp_path / "a.db", tmp_path / "bk"
    env = make_task_env(db, bk)
    tid = _crash_mid_step2(env)
    env.store.request_cancel(tid)
    [rec] = make_task_env(db, bk).store.recover_after_restart()
    assert rec.state is TaskState.CANCELLED


def test_recovery_leaves_waiting_tasks_alone(task_env: TaskEnv) -> None:
    t = new(task_env)
    task_env.store.transition(t.id, TaskState.PLANNING)
    task_env.store.transition(t.id, TaskState.WAITING_APPROVAL)
    assert task_env.store.recover_after_restart() == []
    assert task_env.store.get(t.id).state is TaskState.WAITING_APPROVAL


def test_interrupted_while_planning_replans(task_env: TaskEnv) -> None:
    t = new(task_env)
    task_env.store.transition(t.id, TaskState.PLANNING)
    task_env.store.recover_after_restart()
    ex = StepsExecutor()
    task_env.engine.register("demo", ex)
    once(task_env)
    assert task_env.store.get(t.id).state is TaskState.COMPLETED and ex.plans == 1


# ----------------------------------------------------------------- retries

def test_failed_verification_retries_then_completes(task_env: TaskEnv) -> None:
    task_env.engine.register("demo", StepsExecutor(verify_fails=1))
    t = new(task_env)
    once(task_env)
    mid = task_env.store.get(t.id)
    assert mid.state is TaskState.RETRYING and mid.error_code == "VERIFICATION_FAILED"
    once(task_env)
    assert task_env.store.get(t.id).state is TaskState.COMPLETED


def test_retries_exhausted_fails_permanently(task_env: TaskEnv) -> None:
    task_env.engine.register("demo", StepsExecutor(raise_at=2))
    t = new(task_env)
    for _ in range(3):                          # first run + max_retries=2
        once(task_env)
    final = task_env.store.get(t.id)
    assert final.state is TaskState.FAILED and final.retry_count == 2
    assert final.error_code == "STEP_ERROR" and final.steps[1].status is StepStatus.FAILED
    assert any("FAILED" in text for _, text in task_env.sent)


def test_engine_loop_wakes_on_submit(task_env: TaskEnv) -> None:
    ex = StepsExecutor(steps=1)
    task_env.engine.register("user_request", ex)

    async def go() -> float:
        stop = asyncio.Event()
        loop_task = asyncio.create_task(task_env.engine.run(stop))
        await asyncio.sleep(0.05)
        t0 = asyncio.get_running_loop().time()
        reply = await task_env.commands.submit(msg("hello nova"))
        assert "Task #1" in reply.text
        while task_env.store.get(1).state is not TaskState.COMPLETED:
            await asyncio.sleep(0.01)
        elapsed = asyncio.get_running_loop().time() - t0
        stop.set()
        await asyncio.wait_for(loop_task, 3)
        return elapsed

    assert asyncio.run(go()) < 0.9               # woken, not waiting for idle poll (1s)


# ---------------------------------------------------- commands + history

def test_task_commands_and_history(task_env: TaskEnv) -> None:
    task_env.engine.register("demo", StepsExecutor())
    done = new(task_env, "build click tv")
    once(task_env)
    queued = new(task_env, "second job", ttype="user_request")

    listing = cmd(task_env, "tasks")
    assert f"#{done.id} COMPLETED — build click tv" in listing
    assert f"#{queued.id} RECEIVED — second job" in listing

    detail = cmd(task_env, "task", str(done.id))
    assert "TASK #1 — COMPLETED" in detail and "1. step 1  — DONE" in detail
    assert "Result: 3 steps done" in detail
    assert "পাওয়া যায়নি" in cmd(task_env, "task", "999")
    assert "ব্যবহার" in cmd(task_env, "task", "abc")
    assert "#2" in cmd(task_env, "task", "#2")

    assert "cancel করা হয়েছে" in cmd(task_env, "cancel", str(queued.id))
    assert "আগেই শেষ" in cmd(task_env, "cancel", str(done.id))
    assert "PAUSED" not in cmd(task_env, "tasks")
    assert "Queue paused" in cmd(task_env, "pause")
    assert "(queue ⏸️ PAUSED)" in cmd(task_env, "tasks")
    assert "চালু হয়েছে" in cmd(task_env, "resume")


def test_submit_reports_missing_planner(task_env: TaskEnv) -> None:
    reply = asyncio.run(task_env.commands.submit(msg("Click TV test koro")))
    assert "Task #1" in reply.text and "Phase 8" in reply.text
    assert task_env.store.get(1).request_text == "Click TV test koro"
