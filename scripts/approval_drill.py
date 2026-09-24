"""Phase 4 real-phone drill: a RED action is blocked until the owner decides.

Uses the real bot + owner whitelist, but a throw-away database and dummy
files under <workspace>/approval-drill-*/ (removed afterwards), so the real
task history is untouched. Stop the core service first (only one poller).

    uv run python scripts/approval_drill.py

Round 1: tap ❌ Reject  → dummy file must still exist, task CANCELLED
Round 2: tap ✅ Approve → dummy file deleted exactly once, task COMPLETED
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from channels.base import OutgoingMessage  # noqa: E402
from channels.telegram.api import TelegramConflict  # noqa: E402
from channels.telegram.channel import TelegramChannel  # noqa: E402
from core.bootstrap import bootstrap  # noqa: E402
from core.db.engine import make_sessionmaker  # noqa: E402
from core.db.migrate import check_and_migrate  # noqa: E402
from core.db.models import Approval  # noqa: E402
from core.log import shutdown_logging  # noqa: E402
from core.notify.notifier import MessageType, Notifier  # noqa: E402
from core.orchestrator.commands import CommandRouter, HealthSources  # noqa: E402
from core.orchestrator.security_commands import SecurityCommands  # noqa: E402
from core.orchestrator.task_commands import TaskCommands  # noqa: E402
from core.permissions.approvals import ApprovalManager  # noqa: E402
from core.permissions.audit import AuditTrail  # noqa: E402
from core.permissions.engine import PermissionEngine  # noqa: E402
from core.queue.engine import TaskContext, TaskEngine, Verdict  # noqa: E402
from core.queue.states import TaskState  # noqa: E402
from core.queue.store import TaskStore  # noqa: E402
from core.service import telegram_settings  # noqa: E402

DECISION_TIMEOUT = 280


class DeleteDummy:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.executions = 0

    async def plan(self, ctx: TaskContext) -> list[str]:
        return [f"delete dummy file {self.path.name}"]

    async def run(self, ctx: TaskContext) -> str:
        async def act(prev):  # type: ignore[no-untyped-def]
            await ctx.require("file.delete_important", target=str(self.path),
                              summary="🧪 DRILL — শুধু একটা dummy test file, আসল কিছু না।")
            self.executions += 1
            self.path.unlink()
            return {"deleted": True}
        await ctx.run_step(1, act)
        return f"{self.path.name} deleted"

    async def verify(self, ctx: TaskContext, result: str) -> Verdict:
        return Verdict(not self.path.exists(), "file no longer exists")


async def _main() -> int:
    ctx = bootstrap(worker="core", console_logs=False)
    api, whitelist = telegram_settings(ctx)
    cfg = ctx.config
    work = cfg.path("workspace_dir") / f"approval-drill-{int(time.time())}"
    work.mkdir(parents=True)
    results: list[tuple[str, bool]] = []
    mig = check_and_migrate(work / "drill.db", work / "backups")
    try:
        sessions = make_sessionmaker(mig.engine)
        store = TaskStore(sessions)
        audit = AuditTrail(sessions)

        async def send(chat_id: str, msg: OutgoingMessage) -> None:
            await channel.send(chat_id, msg)   # bound below; used only after that

        notifier = Notifier(cfg.default.notification_throttle, send)
        permissions = PermissionEngine(cfg.permissions, sessions)
        approvals = ApprovalManager(sessions, cfg.permissions.approval, store, audit, notifier)
        engine = TaskEngine(store, notifier, cfg.default.task_engine, permissions, approvals)
        security = SecurityCommands(permissions, approvals, store, audit)
        tasks = TaskCommands(store, engine, 10, cfg.default.agent.timezone)
        router = CommandRouter(cfg.root, HealthSources(), "Nova AI (drill)", tasks, None,
                               security)
        channel = TelegramChannel(api, whitelist, router.handle, poll_timeout=10,
                                  callback_handler=security.on_button)
        owner = sorted(whitelist.chat_ids)[0]

        try:
            # Drain + detect a second poller. Stale taps from an earlier drill get
            # an "Approval পাওয়া যায়নি" toast (their nonces do not exist here).
            stale = await channel.poll_once()
            print(f"drained {stale} stale update(s)", flush=True)
        except TelegramConflict:
            print("FAIL: another poller is running — stop the core service first.")
            return 2

        await notifier.notify(owner, MessageType.INFO,
                              "🧪 Approval drill শুরু\n১ম approval-এ ❌ Reject চাপুন\n"
                              "২য় approval-এ ✅ Approve চাপুন")

        async def round_(name: str, expect_approve: bool) -> None:
            dummy = work / f"{name}.txt"
            dummy.write_text("drill")
            ex = DeleteDummy(dummy)
            engine.register(name, ex)
            tid = store.create(title=f"drill {name}", request_text=name, task_type=name,
                               channel="telegram", chat_id=owner).id
            await engine.run_once()
            t = store.get(tid)
            blocked = (t is not None and t.state is TaskState.WAITING_APPROVAL
                       and dummy.exists() and ex.executions == 0)
            results.append((f"{name}: blocked before decision", blocked))
            print(f"[{name}] waiting for your tap (state={t.state if t else None}, "
                  f"file exists={dummy.exists()})", flush=True)

            deadline = time.time() + DECISION_TIMEOUT
            while time.time() < deadline:
                n = await channel.poll_once()
                if n:
                    print(f"[{name}] received {n} update(s)", flush=True)
                t = store.get(tid)
                if t is not None and t.state is not TaskState.WAITING_APPROVAL:
                    break
            if t is not None and t.state is TaskState.RETRYING:
                await engine.run_once()
                t = store.get(tid)
            state = t.state if t else None
            if expect_approve:
                ok = state is TaskState.COMPLETED and not dummy.exists() and ex.executions == 1
                with sessions() as s:
                    statuses = [a.status for a in s.scalars(
                        select(Approval).where(Approval.task_id == tid))]
                ok = ok and statuses == ["USED"]
                results.append((f"{name}: approve → executed exactly once, approval USED", ok))
            else:
                ok = state is TaskState.CANCELLED and dummy.exists() and ex.executions == 0
                results.append((f"{name}: reject → not executed, task CANCELLED", ok))
            print(f"[{name}] final state={state}, file exists={dummy.exists()}, "
                  f"executions={ex.executions}", flush=True)

        await round_("round1_reject", expect_approve=False)
        await round_("round2_approve", expect_approve=True)
        await channel.acknowledge()

        passed = all(ok for _, ok in results)
        summary = "\n".join(f"{'✅' if ok else '❌'} {n}" for n, ok in results)
        await notifier.notify(owner, MessageType.INFO,
                              f"🧪 Drill {'PASS' if passed else 'FAIL'}\n{summary}")
        print(("DRILL PASS" if passed else "DRILL FAIL") + "\n" + summary)
        return 0 if passed else 1
    finally:
        await api.aclose()
        mig.engine.dispose()
        shutdown_logging()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
