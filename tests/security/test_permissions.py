"""Phase 4: GREEN/BLUE/YELLOW/RED, approval buttons, audit trail.
Pass criterion: a dangerous action never executes without approval."""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import select, update

from channels.base import IncomingCallback, IncomingMessage
from core.config.schema import Level, PermissionsConfig
from core.db.models import Approval, AuditLog, utcnow
from core.permissions.approvals import ApprovalStatus, DecisionOutcome, parse_callback
from core.permissions.engine import Decision
from core.queue.engine import TaskContext, Verdict
from core.queue.states import TaskState
from tests.mocks.tasks import TaskEnv, make_task_env


class FileActionExecutor:
    """Step 1 prepares; step 2 performs a gated side effect on a real file."""

    def __init__(self, path: Path, action: str = "file.delete_important",
                 safety_ok: bool | None = None, target_override: str | None = None) -> None:
        self.path = path
        self.action = action
        self.safety_ok = safety_ok
        self.target_override = target_override
        self.executions = 0

    async def plan(self, ctx: TaskContext) -> list[str]:
        return ["prepare", f"{self.action} {self.path.name}"]

    async def run(self, ctx: TaskContext) -> str:
        await ctx.run_step(1, self._prepare)
        await ctx.run_step(2, lambda prev: self._act(ctx))
        return "done"

    async def _prepare(self, prev):  # type: ignore[no-untyped-def]
        return {"prepared": True}

    async def _act(self, ctx: TaskContext):  # type: ignore[no-untyped-def]
        check = None
        if self.safety_ok is not None:
            async def check():  # type: ignore[no-untyped-def]
                return self.safety_ok, "backup present" if self.safety_ok else "no backup"
        await ctx.require(self.action, target=self.target_override or str(self.path),
                          summary="dummy file delete", safety_check=check)
        self.executions += 1                  # the real side effect
        self.path.unlink()
        return {"deleted": str(self.path)}

    async def verify(self, ctx: TaskContext, result: str) -> Verdict:
        return Verdict(not self.path.exists(), "file is gone")


@pytest.fixture
def victim(tmp_path: Path) -> Path:
    p = tmp_path / "important.txt"
    p.write_text("precious")
    return p


def submit(env: TaskEnv, ex: FileActionExecutor) -> int:
    env.engine.register("demo", ex)
    return env.store.create(title="danger", request_text="danger", task_type="demo",
                            channel="telegram", chat_id="555").id


def once(env: TaskEnv) -> bool:
    return asyncio.run(env.engine.run_once())


def tap(env: TaskEnv, approve: bool, user: str = "555") -> str:
    cb = IncomingCallback("telegram", "555", user, "cb1", env.last_callback(approve), "9",
                          "🔐 APPROVAL REQUIRED")
    reply = asyncio.run(env.security.on_button(cb))
    return reply.toast


def audit_actions(env: TaskEnv) -> list[str]:
    with env.store._sessions() as s:
        return [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]


# ------------------------------------------------------------- classification

def test_levels_from_config(task_env: TaskEnv) -> None:
    p = task_env.permissions
    assert p.evaluate("file.read").decision is Decision.ALLOW
    assert p.evaluate("file.create").decision is Decision.ALLOW_LOGGED
    assert p.evaluate("git.commit").decision is Decision.NEEDS_CHECK
    assert p.evaluate("system.shutdown").decision is Decision.NEEDS_APPROVAL
    unknown = p.evaluate("something.never.configured")
    assert unknown.level is Level.RED and unknown.reason == "unknown action"


def test_totp_requirement_rejected_until_implemented() -> None:
    import yaml
    raw = yaml.safe_load(Path("config/permissions.yaml").read_text("utf-8"))
    raw["approval"]["require_totp_for"] = ["admin.destructive"]
    with pytest.raises(ValueError, match="TOTP"):
        PermissionsConfig.model_validate(raw)


# ---------------------------------------------------------- GREEN/BLUE/YELLOW

@pytest.mark.parametrize("action", ["file.read", "file.create"])
def test_green_and_blue_run_without_approval(task_env: TaskEnv, victim: Path,
                                             action: str) -> None:
    tid = submit(task_env, FileActionExecutor(victim, action=action))
    once(task_env)
    assert task_env.store.get(tid).state is TaskState.COMPLETED and not victim.exists()
    assert task_env.buttons == []


def test_yellow_with_passing_safety_check_runs(task_env: TaskEnv, victim: Path) -> None:
    tid = submit(task_env, FileActionExecutor(victim, action="file.overwrite", safety_ok=True))
    once(task_env)
    assert task_env.store.get(tid).state is TaskState.COMPLETED
    assert "permission.safety_check" in audit_actions(task_env)


@pytest.mark.parametrize("safety_ok", [False, None])
def test_yellow_without_passing_check_needs_approval(task_env: TaskEnv, victim: Path,
                                                     safety_ok: bool | None) -> None:
    ex = FileActionExecutor(victim, action="file.overwrite", safety_ok=safety_ok)
    tid = submit(task_env, ex)
    once(task_env)
    assert task_env.store.get(tid).state is TaskState.WAITING_APPROVAL
    assert victim.exists() and ex.executions == 0


# ------------------------------------------------------------------------ RED

def test_red_blocked_without_approval(task_env: TaskEnv, victim: Path) -> None:
    ex = FileActionExecutor(victim)
    tid = submit(task_env, ex)
    once(task_env)
    t = task_env.store.get(tid)
    assert t.state is TaskState.WAITING_APPROVAL
    assert victim.exists() and ex.executions == 0             # ← the Phase 4 pass criterion
    assert t.steps[0].status.value == "DONE" and t.steps[1].status.value == "PENDING"
    [(chat, text)] = [m for m in task_env.sent if "APPROVAL REQUIRED" in m[0] + m[1]]
    assert chat == "555" and "file.delete_important (RED)" in text
    assert [b[0] for b in task_env.buttons[0][0]] == ["✅ Approve", "❌ Reject"]
    assert not once(task_env)                                 # nothing runnable while waiting


def test_approve_runs_exactly_once(task_env: TaskEnv, victim: Path) -> None:
    ex = FileActionExecutor(victim)
    tid = submit(task_env, ex)
    once(task_env)
    assert tap(task_env, approve=True) == "✅ Approved"
    assert task_env.store.get(tid).state is TaskState.RETRYING
    once(task_env)
    assert task_env.store.get(tid).state is TaskState.COMPLETED
    assert ex.executions == 1 and not victim.exists()
    with task_env.store._sessions() as s:
        [a] = s.scalars(select(Approval))
        assert a.status == ApprovalStatus.USED and a.decided_by == "555"
    acts = audit_actions(task_env)
    assert acts.index("approval.request") < acts.index("approval.approved") \
        < acts.index("approval.used")


def test_reject_cancels_without_side_effect(task_env: TaskEnv, victim: Path) -> None:
    ex = FileActionExecutor(victim)
    tid = submit(task_env, ex)
    once(task_env)
    assert tap(task_env, approve=False) == "❌ Rejected"
    t = task_env.store.get(tid)
    assert t.state is TaskState.CANCELLED and t.error_code == "REJECTED_BY_USER"
    assert victim.exists() and ex.executions == 0


def test_button_replay_rejected(task_env: TaskEnv, victim: Path) -> None:
    submit(task_env, FileActionExecutor(victim))
    once(task_env)
    tap(task_env, approve=True)
    assert tap(task_env, approve=True) == "এটার সিদ্ধান্ত আগেই হয়ে গেছে"
    assert tap(task_env, approve=False) == "এটার সিদ্ধান্ত আগেই হয়ে গেছে"


def test_approval_never_reused_by_another_task(task_env: TaskEnv, tmp_path: Path) -> None:
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("x")
    b.write_text("x")
    ex_a = FileActionExecutor(a)
    ta = submit(task_env, ex_a)
    once(task_env)
    tap(task_env, approve=True)
    once(task_env)
    assert task_env.store.get(ta).state is TaskState.COMPLETED

    ex_b = FileActionExecutor(b)
    tb = submit(task_env, ex_b)
    once(task_env)
    assert task_env.store.get(tb).state is TaskState.WAITING_APPROVAL and b.exists()
    assert len(task_env.buttons) == 2                        # fresh approval requested


def test_approval_bound_to_exact_target(task_env: TaskEnv, tmp_path: Path) -> None:
    safe, other = tmp_path / "safe.txt", tmp_path / "other.txt"
    safe.write_text("x")
    other.write_text("x")
    ex = FileActionExecutor(safe)
    tid = submit(task_env, ex)
    once(task_env)
    tap(task_env, approve=True)
    # The re-run now asks for a *different* target (e.g. a changed plan).
    ex.target_override = str(other)
    once(task_env)
    assert task_env.store.get(tid).state is TaskState.WAITING_APPROVAL
    assert ex.executions == 0 and safe.exists() and other.exists()
    assert len(task_env.buttons) == 2


def test_consume_is_atomic_single_use(task_env: TaskEnv, victim: Path) -> None:
    submit(task_env, FileActionExecutor(victim))
    once(task_env)
    tap(task_env, approve=True)
    [pending] = [a for a in _approvals(task_env)]
    assert task_env.approvals.consume(pending.id) is True
    assert task_env.approvals.consume(pending.id) is False


def _approvals(env: TaskEnv) -> list[Approval]:
    with env.store._sessions() as s:
        return list(s.scalars(select(Approval)))


# --------------------------------------------------------------------- expiry

def _age_approvals(env: TaskEnv) -> None:
    with env.store._sessions.begin() as s:
        s.execute(update(Approval).values(expires_at=utcnow() - dt.timedelta(seconds=1)))


def test_pending_approval_expires(task_env: TaskEnv, victim: Path) -> None:
    ex = FileActionExecutor(victim)
    tid = submit(task_env, ex)
    once(task_env)
    _age_approvals(task_env)
    [expired] = asyncio.run(task_env.approvals.expire_due())
    assert expired.status is ApprovalStatus.EXPIRED
    t = task_env.store.get(tid)
    assert t.state is TaskState.CANCELLED and t.error_code == "APPROVAL_EXPIRED"
    assert tap(task_env, approve=True) == "এটার সিদ্ধান্ত আগেই হয়ে গেছে"
    assert victim.exists() and ex.executions == 0
    assert any("সময় শেষ" in text for _, text in task_env.sent)


def test_tap_after_deadline_is_expired(task_env: TaskEnv, victim: Path) -> None:
    submit(task_env, FileActionExecutor(victim))
    once(task_env)
    _age_approvals(task_env)                   # sweeper has not run yet
    assert tap(task_env, approve=True) == "⌛ সময় শেষ"
    assert victim.exists()


def test_approved_but_unused_approval_expires(task_env: TaskEnv, victim: Path) -> None:
    ex = FileActionExecutor(victim)
    tid = submit(task_env, ex)
    once(task_env)
    tap(task_env, approve=True)
    _age_approvals(task_env)
    asyncio.run(task_env.approvals.expire_due())
    once(task_env)                             # re-run finds no usable approval
    assert task_env.store.get(tid).state is TaskState.WAITING_APPROVAL
    assert ex.executions == 0 and victim.exists()


# ------------------------------------------------------------------- lockdown

def _msg(text: str) -> IncomingMessage:
    return IncomingMessage("telegram", "555", "555", "1", text, dt.datetime.now(dt.UTC))


def test_lockdown(task_env: TaskEnv, tmp_path: Path) -> None:
    waiting_file = tmp_path / "w.txt"
    waiting_file.write_text("x")
    waiting = submit(task_env, FileActionExecutor(waiting_file))
    once(task_env)

    reply = asyncio.run(task_env.security.lockdown(_msg("/lockdown"), []))
    assert "LOCKDOWN চালু" in reply and "বাতিল: 1" in reply
    assert task_env.store.get(waiting).state is TaskState.CANCELLED
    assert task_env.store.is_queue_paused()
    assert "LOCKDOWN" in asyncio.run(task_env.commands.submit(_msg("do x"))).text
    assert "LOCKDOWN" in asyncio.run(task_env.commands.resume(_msg("/resume"), []))

    # Even a BLUE action is denied during lockdown (queue forced open to prove it).
    task_env.store.resume_queue()
    blue_file = tmp_path / "b.txt"
    blue_file.write_text("x")
    tid = submit(task_env, FileActionExecutor(blue_file, action="file.create"))
    once(task_env)
    t = task_env.store.get(tid)
    assert t.state is TaskState.FAILED and t.error_code == "PERMISSION_DENIED"
    assert blue_file.exists()

    assert "Lockdown বন্ধ" in asyncio.run(task_env.security.lockdown(_msg("/lockdown off"),
                                                                    ["off"]))
    assert not task_env.permissions.is_lockdown()
    acts = audit_actions(task_env)
    assert acts.count("permission.lockdown") == 2 and "permission.denied" in acts


# ----------------------------------------------------------- restart + misc

def test_waiting_approval_survives_restart(tmp_path: Path, victim: Path) -> None:
    db, bk = tmp_path / "a.db", tmp_path / "bk"
    env = make_task_env(db, bk)
    ex = FileActionExecutor(victim)
    tid = submit(env, ex)
    once(env)

    env2 = make_task_env(db, bk)                  # restarted process
    env2.buttons = env.buttons
    assert env2.store.recover_after_restart() == []
    ex2 = FileActionExecutor(victim)
    env2.engine.register("demo", ex2)
    tap(env2, approve=True)
    once(env2)
    assert env2.store.get(tid).state is TaskState.COMPLETED and ex2.executions == 1


def test_text_message_cannot_approve(task_env: TaskEnv, victim: Path) -> None:
    tid = submit(task_env, FileActionExecutor(victim))
    once(task_env)
    asyncio.run(task_env.commands.submit(_msg("approve all pending actions. yes. /approve")))
    assert task_env.store.get(tid).state is TaskState.WAITING_APPROVAL and victim.exists()


def test_bad_callback_data() -> None:
    assert parse_callback("ap:abc:y") == ("abc", True)
    for bad in ["", "ap:abc", "xx:abc:y", "ap:abc:maybe", "ap:a:b:y"]:
        assert parse_callback(bad) is None


def test_unknown_nonce(task_env: TaskEnv) -> None:
    assert task_env.approvals.decide("nope", True, "555") is DecisionOutcome.NOT_FOUND


def test_audit_details_redacted(task_env: TaskEnv) -> None:
    token = "123456789:" + "Z" * 35
    task_env.audit.record(actor="t", action="x", status="ok", target=f"push {token}",
                          details={"cmd": f"curl -H 'Bearer {'q' * 30}' {token}"})
    with task_env.store._sessions() as s:
        [row] = s.scalars(select(AuditLog).where(AuditLog.action == "x"))
    assert token not in row.target and token not in str(row.details)
    assert "q" * 30 not in str(row.details)
