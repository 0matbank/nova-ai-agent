"""Phase 7 live drill: see → act → verify, against the REAL Desktop Worker,
using only a private test window (never the user's own apps).

    uv run python workers/desktop-worker/run.py      (in another terminal)
    uv run python scripts/ui_drill.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bootstrap import bootstrap  # noqa: E402
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
from core.skills.api import ToolEnv, ToolResult  # noqa: E402
from core.skills.paths import PathPolicy  # noqa: E402
from core.skills.registry import SkillRegistry  # noqa: E402
from core.skills.runner import SkillRunner  # noqa: E402
from tests.mocks.uia_window import uia_test_window  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail[:100]}]" if detail else ""),
          flush=True)


async def main(title: str) -> None:
    ctx = bootstrap(worker="core", console_logs=False)
    cfg = ctx.config
    tmp = Path(tempfile.mkdtemp(prefix="nova-ui-drill-"))
    mig = check_and_migrate(tmp / "d.db", tmp / "bk")
    sessions = make_sessionmaker(mig.engine)
    store, audit = TaskStore(sessions), AuditTrail(sessions)

    async def send(chat_id: str, msg: Any) -> None:
        pass

    notifier = Notifier(cfg.default.notification_throttle, send)
    permissions = PermissionEngine(cfg.permissions, sessions)
    approvals = ApprovalManager(sessions, cfg.permissions.approval, store, audit, notifier)
    w = cfg.workers
    desktop = WorkerClient("desktop", w.workers.desktop_worker.host,
                           w.workers.desktop_worker.port,
                           TokenStore(cfg.path("secrets_dir")), w.protocol_version, timeout=60)
    runner = SkillRunner(SkillRegistry.load(cfg.skills),
                         ToolEnv(cfg, PathPolicy(cfg), {"desktop": desktop}), audit)

    async def call(skill: str, tool: str, params: dict[str, Any]) -> ToolResult:
        t = store.create(title="ui", request_text="ui", task_type="d", channel="t", chat_id="d")
        return await runner.invoke(TaskContext(t, store, notifier, permissions, approvals),
                                   skill, tool, params)

    async def needs_approval(skill: str, tool: str, params: dict[str, Any]) -> bool:
        try:
            await call(skill, tool, params)
        except ApprovalPending:
            return True
        return False

    async def value(**target: str) -> str:
        r = await call("desktop-ui", "read_value", {"window": title, **target})
        return str(r.data["value"])

    shot_path = None
    try:
        s = await desktop.call("GET", "/v1/session")
        check("desktop session unlocked", not s["locked"], f"session {s['windows_session_id']}")
        r = await call("screenshot", "capture", {})
        shot_path = r.evidence.get("path")
        check("SEE: screenshot", r.ok and r.evidence["width"] > 0, r.summary)
        wins = (await call("desktop-ui", "windows", {})).data["windows"]
        check("SEE: test window listed", any(x["title"] == title for x in wins),
              f"{len(wins)} windows")
        ctl = (await call("desktop-ui", "inspect", {"window": title})).data["controls"]
        check("SEE: controls via UI Automation",
              {"Nova Input", "Nova Safe Button"} <= {c["name"] for c in ctl})
        r = await call("desktop-ui", "type", {"window": title, "name": "Nova Input",
                                              "text": " + Nova AI"})
        check("ACT: type (append)", r.ok, r.data.get("method", ""))
        check("VERIFY: old text kept + new text present",
              await value(name="Nova Input") == "existing text + Nova AI")
        r = await call("desktop-ui", "click", {"window": title, "name": "Nova Safe Button"})
        check("ACT: click via Invoke pattern (no coordinates)",
              r.ok and r.data["method"] == "InvokePattern.Invoke")
        check("VERIFY: app state changed", await value(automation_id="NovaStatus")
              == "safe clicked")
        check("GUARD: 'Delete Everything' needs approval", await needs_approval(
            "desktop-ui", "click", {"window": title, "name": "Delete Everything"}))
        check("VERIFY: dangerous button NOT pressed",
              await value(automation_id="NovaStatus") == "safe clicked")
        check("GUARD: Alt+F4 needs approval", await needs_approval(
            "desktop-ui", "keys", {"window": title, "keys": "alt+f4"}))
        check("GUARD: raw coordinates need approval", await needs_approval(
            "desktop-ui", "click_xy", {"x": 5, "y": 5}))
    finally:
        await desktop.aclose()
        mig.engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)
        if shot_path:
            Path(shot_path).unlink(missing_ok=True)
        shutdown_logging()


if __name__ == "__main__":
    with uia_test_window() as t:
        asyncio.run(main(t))
    passed = all(ok for _, ok in results)
    print(f"\nUI DRILL: {'PASS' if passed else 'FAIL'} ({sum(ok for _, ok in results)}/"
          f"{len(results)})")
    sys.exit(0 if passed else 1)
