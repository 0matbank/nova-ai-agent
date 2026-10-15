"""Task engine: DB-backed queue + dispatcher (plan §26, §29, §30).

One task runs at a time (multiple simultaneous agents are V2, plan §43).
Executors plan steps and run them through `TaskContext.run_step`, which
checkpoints each step; a resumed task skips steps already DONE. Cancel and
pause are cooperative — honoured at the next checkpoint, never mid-step, so a
real side effect is never cut in half. A task only reaches COMPLETED after
its executor's `verify` passes (VERIFYING → COMPLETED).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from core.config.schema import TaskEngineSection
from core.log import get_logger, log_context
from core.notify.notifier import MessageType, Notifier
from core.permissions.approvals import ApprovalManager, ApprovalStatus, fingerprint
from core.permissions.engine import Decision, PermissionDenied, PermissionEngine
from core.queue.states import TaskState
from core.queue.store import StepStatus, StepView, TaskStore, TaskView
from core.skills.api import PolicyDenied
from core.skills.desktop import NEEDS_DESKTOP, DesktopUnavailable

T = TypeVar("T")
_log = get_logger("tasks")


class TaskCancelled(Exception):
    pass


class TaskPaused(Exception):
    pass


class ApprovalPending(Exception):
    """Raised by `require` — the task parks in WAITING_APPROVAL; the step
    re-runs from its start once the owner approves."""

    def __init__(self, approval_id: int) -> None:
        super().__init__(f"waiting for approval #{approval_id}")
        self.approval_id = approval_id


class TaskBlocked(Exception):
    """BLOCKING (plan §17A): only the owner can unblock it (login expired, a
    CAPTCHA, …). Reported immediately and never retried."""


SafetyCheck = Callable[[], Awaitable[tuple[bool, str]]]


@dataclass(frozen=True)
class Verdict:
    passed: bool
    detail: str


class Executor(Protocol):
    async def plan(self, ctx: TaskContext) -> list[str]: ...
    async def run(self, ctx: TaskContext) -> str: ...
    async def verify(self, ctx: TaskContext, result: str) -> Verdict: ...


StepFn = Callable[[dict[str, Any] | None], Awaitable[dict[str, Any] | None]]


class TaskContext:
    def __init__(self, task: TaskView, store: TaskStore, notifier: Notifier | None,
                 permissions: PermissionEngine | None = None,
                 approvals: ApprovalManager | None = None) -> None:
        self.task = task
        self.store = store
        self.notifier = notifier
        self.permissions = permissions
        self.approvals = approvals
        self.steps: tuple[StepView, ...] = task.steps
        self.current_seq: int | None = None

    async def require(self, action: str, target: str = "", summary: str = "",
                      details: dict[str, Any] | None = None,
                      safety_check: SafetyCheck | None = None) -> None:
        """Permission gate (plan §18). Call immediately BEFORE a side effect.
        Returns only if the action may proceed right now."""
        if self.permissions is None or self.approvals is None:
            raise PermissionDenied(action, "permission engine unavailable")
        ev = self.permissions.evaluate(action)
        audit = self.approvals.audit
        tid = self.task.id
        if ev.decision is Decision.DENY:
            audit.record(actor="task", action="permission.denied", status="denied",
                         target=f"{action} {target}", task_id=tid, details={"reason": ev.reason})
            raise PermissionDenied(action, ev.reason)
        if ev.decision is Decision.ALLOW:
            return
        if ev.decision is Decision.ALLOW_LOGGED:
            _log.info(f"BLUE action allowed: {action} {target}",
                      extra={"task_id": tid, "action": action, "status": "allowed"})
            return
        if ev.decision is Decision.NEEDS_CHECK:
            if safety_check is not None:
                ok, why = await safety_check()
                audit.record(actor="task", action="permission.safety_check",
                             status="passed" if ok else "failed", target=f"{action} {target}",
                             task_id=tid, details={"detail": why})
                if ok:
                    return
                summary = f"{summary}\n⚠️ Safety check failed: {why}".strip()
            else:
                summary = f"{summary}\n⚠️ No safety check available for this action.".strip()

        # RED, or YELLOW that did not pass its check → explicit approval.
        fp = fingerprint(action, target, details)
        approved = self.approvals.find(tid, fp, ApprovalStatus.APPROVED)
        if approved is not None and self.approvals.consume(approved.id):
            audit.record(actor="task", action="approval.used", status="used",
                         target=f"{action} {target}", task_id=tid,
                         details={"approval_id": approved.id})
            return
        req = await self.approvals.request(task_id=tid, chat_id=self.task.chat_id,
                                           action=action, level=ev.level, target=target,
                                           summary=summary, details=details)
        raise ApprovalPending(req.id)

    async def checkpoint(self) -> None:
        """Honour cancel/pause requests. Call between side effects only."""
        await asyncio.sleep(0)
        cancel, pause = self.store.flags(self.task.id)
        if cancel:
            raise TaskCancelled()
        if pause:
            raise TaskPaused()

    async def interruptible(self, aw: Awaitable[T], every: float = 2.0) -> T:
        """Await a long operation (a coding agent run, tests) while honouring /cancel
        and /pause within seconds — the operation is cancelled, not waited for
        (audit 2026-10-15: /cancel only took effect after the whole step)."""
        job: asyncio.Future[T] = asyncio.ensure_future(aw)
        try:
            while True:
                done, _ = await asyncio.wait({job}, timeout=every)
                if done:
                    return job.result()
                await self.checkpoint()          # raises TaskCancelled / TaskPaused
        finally:
            if not job.done():
                job.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await job

    def remember(self, data: dict[str, Any]) -> None:
        """Save the running step's portable state (e.g. which provider failed and what
        was already changed) so a retry, a restart or another provider continues
        instead of starting over (plan §8.5, §9.2)."""
        if self.current_seq is not None:
            self.store.save_partial(self.task.id, self.current_seq, data)

    def recalled(self) -> dict[str, Any] | None:
        """The portable state an earlier attempt of the running step left behind."""
        step = next((s for s in self.steps if s.seq == self.current_seq), None)
        cp = step.checkpoint if step is not None else None
        partial = cp.get("partial") if isinstance(cp, dict) else None
        return partial if isinstance(partial, dict) else None

    async def run_step(self, seq: int, fn: StepFn) -> dict[str, Any] | None:
        """Run step `seq` unless already DONE; returns the step's checkpoint.
        `fn` receives the previous step's checkpoint data."""
        by_seq = {s.seq: s for s in self.steps}
        step = by_seq.get(seq)
        if step is None:
            raise KeyError(f"step {seq} not in plan")
        if step.status is StepStatus.DONE:
            return step.checkpoint
        await self.checkpoint()
        prev = by_seq.get(seq - 1)
        self.current_seq = seq
        self.store.start_step(self.task.id, seq)
        try:
            data = await fn(prev.checkpoint if prev else None)
        except (ApprovalPending, TaskCancelled, TaskPaused, DesktopUnavailable):
            self.store.reset_step(self.task.id, seq)   # not a failure; step re-runs later
            raise
        except Exception as e:
            self.store.fail_step(self.task.id, seq, f"{type(e).__name__}: {e}")
            raise
        self.store.complete_step(self.task.id, seq, data)
        refreshed = self.store.get(self.task.id)
        if refreshed is not None:
            self.steps = refreshed.steps
        return data

    async def progress(self, text: str) -> None:
        if self.notifier is not None:
            await self.notifier.notify(self.task.chat_id, MessageType.PROGRESS,
                                       f"⏳ Task #{self.task.id}: {text}", task_id=self.task.id)


class TaskEngine:
    def __init__(self, store: TaskStore, notifier: Notifier | None,
                 settings: TaskEngineSection, permissions: PermissionEngine | None = None,
                 approvals: ApprovalManager | None = None) -> None:
        self.store = store
        self.notifier = notifier
        self.settings = settings
        self.permissions = permissions
        self.approvals = approvals
        if approvals is not None:
            approvals.on_task_released.append(self.wake)
        self.executors: dict[str, Executor] = {}
        self._wake = asyncio.Event()
        self.current_task_id: int | None = None
        self.heartbeat: float | None = None        # last loop round (for /status)
        self.queue_error: str | None = None

    def register(self, task_type: str, executor: Executor) -> None:
        self.executors[task_type] = executor

    def wake(self) -> None:
        self._wake.set()

    async def run(self, stop: asyncio.Event) -> None:
        failures = 0
        while not stop.is_set():
            try:
                ran = await self.run_once()
            except Exception as e:
                # e.g. "database is locked" or a disk error while picking the next task:
                # the dispatcher must never die silently (audit 2026-10-15) — alert, retry.
                failures += 1
                self.queue_error = f"{type(e).__name__}: {e}"[:200]
                if failures in (1, 10, 100):        # alert, but not on every retry
                    _log.critical(f"task engine cannot read the queue ({failures}x) — "
                                  f"retrying: {self.queue_error}",
                                  extra={"action": "task.engine", "status": "error"})
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=min(60.0, 2.0 * failures))
                continue
            failures, self.queue_error = 0, None
            self.heartbeat = time.time()
            if ran:
                continue
            self._wake.clear()
            stopper = asyncio.create_task(stop.wait())
            waker = asyncio.create_task(self._wake.wait())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.wait({stopper, waker}, return_when=asyncio.FIRST_COMPLETED),
                    timeout=self.settings.idle_poll_seconds,
                )
            for t in (stopper, waker):
                t.cancel()

    async def run_once(self) -> bool:
        if self.store.is_queue_paused():
            return False
        task = self.store.next_runnable(self.executors)
        if task is None:
            return False
        self.current_task_id = task.id
        try:
            with log_context(task_id=str(task.id)):
                await self._execute(task)
        except Exception:
            # An engine bug must not kill the dispatcher loop; park the task.
            _log.critical("task engine internal error", exc_info=True,
                          extra={"action": "task.engine", "status": "error"})
            with contextlib.suppress(Exception):
                self.store.transition(task.id, TaskState.FAILED, error_code="ENGINE_ERROR")
        finally:
            self.current_task_id = None
        return True

    async def _execute(self, task: TaskView) -> None:
        executor = self.executors[task.task_type]
        store = self.store
        ctx = TaskContext(task, store, self.notifier, self.permissions, self.approvals)
        try:
            if task.state is TaskState.RECEIVED:
                ctx.task = store.transition(task.id, TaskState.PLANNING)
            if not ctx.steps:     # new task, or one interrupted while planning
                await ctx.checkpoint()
                titles = await executor.plan(ctx)
                ctx.steps = store.ensure_plan(task.id, titles)
            ctx.task = store.transition(task.id, TaskState.RUNNING)
            result = await executor.run(ctx)
            await ctx.checkpoint()
            ctx.task = store.transition(task.id, TaskState.VERIFYING)
            verdict = await executor.verify(ctx, result)
        except TaskCancelled:
            store.transition(task.id, TaskState.CANCELLED, error_code="CANCELLED_BY_USER")
            await self._tell(task, MessageType.INFO, f"🛑 Task #{task.id} cancel হয়েছে।")
            return
        except TaskPaused:
            store.transition(task.id, TaskState.PAUSED)
            await self._tell(task, MessageType.INFO,
                             f"⏸️ Task #{task.id} pause হয়েছে (last checkpoint সংরক্ষিত)।")
            return
        except ApprovalPending as e:
            store.transition(task.id, TaskState.WAITING_APPROVAL, error_code=None)
            _log.info(str(e), extra={"action": "task.approval", "status": "waiting"})
            return
        except DesktopUnavailable as e:
            store.transition(task.id, TaskState.WAITING_DESKTOP, error_code=None,
                             error_message=e.reason)
            await self._tell(task, MessageType.USER_INPUT_REQUIRED,
                             f"🖥️ Task #{task.id}: {NEEDS_DESKTOP}")
            return
        except TaskBlocked as e:
            store.transition(task.id, TaskState.FAILED, error_code="BLOCKED_NEEDS_USER",
                             error_message=str(e))
            await self._tell(task, MessageType.USER_INPUT_REQUIRED, f"🧩 Task #{task.id}: {e}")
            return
        except (PermissionDenied, PolicyDenied) as e:
            store.transition(task.id, TaskState.FAILED, error_code="PERMISSION_DENIED",
                             error_message=str(e))
            await self._tell(task, MessageType.TASK_FAILED_PERMANENTLY,
                             f"⛔ Task #{task.id} blocked: {e}")
            return
        except Exception as e:
            _log.exception("task step failed", extra={"action": "task.run", "status": "error"})
            self._retry_or_fail(task, "STEP_ERROR", f"{type(e).__name__}: {e}")
            await self._report_if_failed(task)
            return

        if verdict.passed:
            store.transition(task.id, TaskState.COMPLETED, result_summary=result,
                             error_code=None, error_message=None)
            await self._tell(task, MessageType.TASK_COMPLETED,
                             f"✅ TASK #{task.id} COMPLETE\n{result}\nVerifier: {verdict.detail}")
        else:
            self._retry_or_fail(task, "VERIFICATION_FAILED", verdict.detail)
            await self._report_if_failed(task)

    def _retry_or_fail(self, task: TaskView, code: str, message: str) -> None:
        current = self.store.get(task.id)
        assert current is not None
        if current.retry_count < self.settings.max_retries:
            self.store.transition(task.id, TaskState.RETRYING, retry_count=current.retry_count + 1,
                                  error_code=code, error_message=message)
        else:
            self.store.transition(task.id, TaskState.FAILED, error_code=code,
                                  error_message=message)

    async def _report_if_failed(self, task: TaskView) -> None:
        current = self.store.get(task.id)
        if current is not None and current.state is TaskState.FAILED:
            await self._tell(task, MessageType.TASK_FAILED_PERMANENTLY,
                             f"❌ Task #{task.id} FAILED ({current.error_code})\n"
                             f"{current.error_message or ''}")

    async def _tell(self, task: TaskView, kind: MessageType, text: str) -> None:
        if self.notifier is not None:
            await self.notifier.notify(task.chat_id, kind, text, task_id=task.id)
