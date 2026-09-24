"""Phase 6 live drill on the REAL PC: basic safe PC control.

Uses the real config, workspace and Desktop Worker (start it first), with a
throw-away task DB. RED actions are only checked for being BLOCKED — nothing
is approved here. Everything the drill creates is removed afterwards.

    uv run python workers/desktop-worker/run.py      (in another terminal)
    uv run python scripts/pc_control_drill.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bootstrap import bootstrap  # noqa: E402
from core.config.schema import NotificationThrottle  # noqa: E402
from core.db.engine import make_sessionmaker  # noqa: E402
from core.db.migrate import check_and_migrate  # noqa: E402
from core.ipc.client import WorkerClient  # noqa: E402
from core.ipc.token import TokenStore  # noqa: E402
from core.log import shutdown_logging  # noqa: E402
from core.notify.notifier import Notifier  # noqa: E402
from core.permissions.approvals import ApprovalManager  # noqa: E402
from core.permissions.audit import AuditTrail  # noqa: E402
from core.permissions.engine import PermissionEngine  # noqa: E402
from core.queue.engine import ApprovalPending, TaskContext  # noqa: E402
from core.queue.store import TaskStore  # noqa: E402
from core.skills.api import PolicyDenied, ToolEnv, ToolResult  # noqa: E402
from core.skills.paths import PathPolicy  # noqa: E402
from core.skills.registry import SkillRegistry  # noqa: E402
from core.skills.runner import SkillRunner  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail[:110]))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail[:110]}]" if detail else ""),
          flush=True)


async def main() -> int:
    ctx = bootstrap(worker="core", console_logs=False)
    cfg = ctx.config
    tmp = Path(tempfile.mkdtemp(prefix="nova-drill-"))
    mig = check_and_migrate(tmp / "drill.db", tmp / "bk")
    sessions = make_sessionmaker(mig.engine)
    store = TaskStore(sessions)
    audit = AuditTrail(sessions)

    async def send(chat_id: str, msg: Any) -> None:   # approval requests are just recorded
        pass

    policy: NotificationThrottle = cfg.default.notification_throttle
    notifier = Notifier(policy, send)
    permissions = PermissionEngine(cfg.permissions, sessions)
    approvals = ApprovalManager(sessions, cfg.permissions.approval, store, audit, notifier)
    w = cfg.workers
    desktop = WorkerClient("desktop", w.workers.desktop_worker.host,
                           w.workers.desktop_worker.port,
                           TokenStore(cfg.path("secrets_dir")), w.protocol_version)
    runner = SkillRunner(SkillRegistry.load(cfg.skills),
                         ToolEnv(cfg, PathPolicy(cfg), {"desktop": desktop}), audit)
    drill = cfg.path("workspace_dir") / f"pc-drill-{int(time.time())}"
    backups_before = set((cfg.path("backups_dir") / "file-backups").glob("*"))

    async def call(skill: str, tool: str, params: dict[str, Any]) -> ToolResult:
        t = store.create(title="drill", request_text="drill", task_type="drill",
                         channel="telegram", chat_id="drill")
        c = TaskContext(t, store, notifier, permissions, approvals)
        return await runner.invoke(c, skill, tool, params)

    async def blocked(skill: str, tool: str, params: dict[str, Any]) -> str:
        try:
            await call(skill, tool, params)
        except ApprovalPending:
            return "approval"
        except PolicyDenied:
            return "denied"
        return "ran"

    try:
        r = await call("windows", "status", {})
        check("windows.status (CPU/GPU/RAM/disk)", r.ok and "CPU:" in r.summary,
              r.summary.replace("\n", " | "))
        r = await call("windows", "system_info", {})
        check("windows.system_info", r.ok, r.summary)
        r = await call("windows", "processes", {"limit": 3})
        check("windows.processes", r.ok and len(r.data["processes"]) == 3, r.summary)

        r = await call("files", "mkdir", {"path": str(drill)})
        check("files.mkdir (BLUE)", r.ok and drill.is_dir())
        f = drill / "নোট.txt"
        r = await call("files", "write", {"path": str(f), "content": "Nova AI drill v1"})
        check("files.write create (BLUE)", r.ok and f.read_text("utf-8") == "Nova AI drill v1")
        r = await call("files", "write", {"path": str(f), "content": "v2", "mode": "overwrite"})
        new_backups = set((cfg.path("backups_dir") / "file-backups").glob("*")) - backups_before
        check("files.write overwrite (YELLOW, backup first)",
              r.ok and f.read_text("utf-8") == "v2" and bool(new_backups))
        r = await call("files", "read_text", {"path": str(f)})
        check("files.read_text (untrusted)", r.ok and r.untrusted and r.data["text"] == "v2")
        r = await call("files", "copy", {"source": str(f), "destination": str(drill / "copy.txt")})
        check("files.copy", r.ok and (drill / "copy.txt").exists())
        r = await call("files", "move", {"source": str(drill / "copy.txt"),
                                         "destination": str(drill / "renamed.txt")})
        check("files.move / rename", r.ok and (drill / "renamed.txt").exists()
              and not (drill / "copy.txt").exists())
        r = await call("search", "find", {"root": str(drill), "pattern": "*.txt"})
        check("search.find", r.ok and len(r.data["matches"]) == 2, r.summary)
        r = await call("search", "duplicates", {"root": str(drill)})
        check("search.duplicates", r.ok and len(r.data["groups"]) == 1, r.summary)
        r = await call("files", "delete", {"path": str(drill / "renamed.txt")})
        check("files.delete in workspace (BLUE)", r.ok and not (drill / "renamed.txt").exists())

        env_file = cfg.path("secrets_dir") / ".env"
        check("secrets/.env read refused", await blocked("files", "read_text",
                                                         {"path": str(env_file)}) == "denied")
        check("PowerShell touching secrets refused", await blocked(
            "powershell", "run", {"command": f"Get-Content '{env_file}'"}) == "denied")
        check("Windows folder write refused", await blocked(
            "files", "write", {"path": r"C:\Windows\nova.txt", "content": "x"}) == "denied")

        r = await call("powershell", "run", {"command": "Get-Date -Format 'yyyy-MM-dd'"})
        check("powershell read-only (GREEN)", r.ok and r.data["stdout"].strip()[:2] == "20",
              r.data["stdout"].strip())
        ps_target = drill / "ps-made.txt"
        verdict = await blocked("powershell", "run",
                                {"command": f"New-Item -Path '{ps_target}' -ItemType File"})
        check("powershell New-Item → approval, not run",
              verdict == "approval" and not ps_target.exists())
        r = await call("terminal", "run", {"program": "whoami"})
        check("terminal whoami (GREEN)", r.ok, r.data["stdout"].strip())
        verdict = await blocked("terminal", "run",
                                {"program": sys.executable, "args": ["-c", "print(1)"]})
        check("terminal arbitrary program → approval", verdict == "approval")
        verdict = await blocked("app-control", "kill", {"app": "notepad"})
        check("app kill → approval", verdict == "approval")

        r = await call("app-control", "open", {"app": "notepad"})
        check("app open notepad (verified by window)", r.ok, r.summary)
        await asyncio.sleep(1)
        r = await call("app-control", "close", {"app": "notepad"})
        check("app close notepad (verified gone)", r.ok, r.summary)
    finally:
        await desktop.aclose()
        mig.engine.dispose()
        shutil.rmtree(drill, ignore_errors=True)
        for b in set((cfg.path("backups_dir") / "file-backups").glob("*")) - backups_before:
            shutil.rmtree(b, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
        shutdown_logging()

    passed = all(ok for _, ok, _ in results)
    print(f"\nPC CONTROL DRILL: {'PASS' if passed else 'FAIL'} "
          f"({sum(ok for _, ok, _ in results)}/{len(results)})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
