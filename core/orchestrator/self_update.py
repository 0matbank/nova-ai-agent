"""/update and /restart-agent (Phase 23, plan §27, §32A).

/update is a task (so it gets the normal RED approval, audit and verifier):
  1 check   — Nova runs under the supervisor, the code folder is clean, `git fetch`
              finds newer commits that fast-forward the current one
  2 approve — the owner sees the commits and approves system.self_update; Nova writes
              the request for the supervisor and goes offline (state flushed)
  — the supervisor backs up the DB, fast-forwards, `uv sync`, runs the preflight,
    starts the new version and waits for its health; anything fails → automatic
    rollback (code, packages, DB) and the old version starts again —
  3 result  — after the restart this same task reads the supervisor's result
Nova never edits its own code: an update only installs commits from the owner's repo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from channels.base import IncomingMessage, OutgoingMessage
from core.config.schema import AppConfig
from core.lifecycle import (
    EXIT_RESTART,
    EXIT_UPDATE,
    UPDATE_REQUEST,
    UPDATE_RESULT,
    git,
    head_commit,
    read_json,
    run_dir,
    supervised,
    write_json,
)
from core.queue.engine import TaskContext, TaskEngine, TaskInterrupted, Verdict, VerdictStatus
from core.queue.store import TaskStore

SELF_UPDATE = "self_update"
NOT_SUPERVISED = ("Nova এখন supervisor ছাড়া চলছে, তাই নিজে restart/update করতে পারবে না।\n"
                  "PC-তে চালান: `uv run python workers/supervisor/run.py`")
RequestExit = Callable[[int, str], None]


def update_source(cfg: AppConfig) -> tuple[str, str]:
    u = cfg.default.update
    import os
    return os.environ.get("NOVA_UPDATE_REMOTE", u.remote), os.environ.get(
        "NOVA_UPDATE_BRANCH", u.branch)


def check_update(app_dir: Path, remote: str, branch: str) -> dict[str, Any]:
    """What /update would install. Never changes the working tree."""
    head = head_commit(app_dir)
    rc, dirty = git(app_dir, "status", "--porcelain", "--untracked-files=no")
    if rc != 0:
        return {"ok": False, "why": f"git status failed: {dirty[:200]}"}
    if dirty:
        return {"ok": False, "why": "code folder-এ commit-না-করা বদল আছে — আগে সেগুলো "
                                    "commit/বাতিল করতে হবে:\n" + dirty[:300]}
    rc, out = git(app_dir, "fetch", "--quiet", remote, branch, timeout=180)
    if rc != 0:
        return {"ok": False, "why": f"git fetch {remote} {branch} হয়নি: {out[:200]}"}
    rc, target = git(app_dir, "rev-parse", "FETCH_HEAD")
    if rc != 0:
        return {"ok": False, "why": "fetched commit পড়া যায়নি"}
    if target == head:
        return {"ok": True, "from": head, "to": head, "commits": []}
    rc, _ = git(app_dir, "merge-base", "--is-ancestor", head, target)
    if rc != 0:
        return {"ok": False, "why": f"{remote}/{branch} বর্তমান commit-এর পরের নয় "
                                    "(fast-forward হয় না) — Nova এটা install করবে না"}
    rc, log = git(app_dir, "log", "--format=%h %s", f"{head}..{target}")
    return {"ok": True, "from": head, "to": target,
            "commits": [line for line in log.splitlines() if line][:30]}


class SelfUpdateExecutor:
    def __init__(self, cfg: AppConfig, request_exit: RequestExit,
                 app_dir: Path | None = None) -> None:
        self.cfg = cfg
        self.app_dir = app_dir or cfg.app_dir
        self.request_exit = request_exit
        self.run_dir = run_dir(cfg.path("data_dir"))

    async def plan(self, ctx: TaskContext) -> list[str]:
        return ["check", "approve", "result"]

    async def run(self, ctx: TaskContext) -> str:
        remote, branch = update_source(self.cfg)

        async def check(prev: dict[str, Any] | None) -> dict[str, Any]:
            if not supervised():
                return {"ok": False, "why": NOT_SUPERVISED}
            return await asyncio.to_thread(check_update, self.app_dir, remote, branch)

        info = await ctx.run_step(1, check) or {}
        if not info.get("ok"):
            return f"⚠️ Update হয়নি: {info.get('why')}"
        if info["from"] == info["to"]:
            return f"✅ Nova already up to date ({info['to'][:8]})"

        async def approve(prev: dict[str, Any] | None) -> dict[str, Any]:
            commits = "\n".join(f"• {c}" for c in info["commits"][:12])
            await ctx.require("system.self_update", target=f"{info['from'][:8]}→{info['to'][:8]}",
                              summary=f"Nova update: {len(info['commits'])}টা নতুন commit\n"
                                      f"{commits}\nNova কিছুক্ষণ offline থাকবে; চলমান কাজ "
                                      "checkpoint থেকে আবার চলবে। ব্যর্থ হলে নিজে আগের "
                                      "version-এ ফিরে যাবে।",
                              details={"from": info["from"], "to": info["to"]})
            write_json(self.run_dir / UPDATE_REQUEST,
                       {"task_id": ctx.task.id, "from": info["from"], "to": info["to"],
                        "remote": remote, "branch": branch})
            return {"requested": True}

        await ctx.run_step(2, approve)
        result = read_json(self.run_dir / UPDATE_RESULT)
        if result is None or result.get("task_id") != ctx.task.id:
            # hand over to the supervisor; this task resumes at step 3 after the restart
            self.request_exit(EXIT_UPDATE, f"update {info['from'][:8]}→{info['to'][:8]}")
            raise TaskInterrupted()

        async def report(prev: dict[str, Any] | None) -> dict[str, Any]:
            current: dict[str, Any] = dict(result)
            # the new version is running but still proving itself to the supervisor; if
            # it fails, the supervisor stops us and the OLD version reports the rollback
            limit = self.cfg.default.update.health_timeout_seconds + \
                self.cfg.default.update.stable_seconds + 60
            waited = 0
            while current.get("status") == "pending_health" and waited < limit:
                await asyncio.sleep(2)
                await ctx.checkpoint()
                waited += 2
                current = read_json(self.run_dir / UPDATE_RESULT) or current
            return current

        res = await ctx.run_step(3, report) or {}
        if res.get("status") == "updated":
            return (f"✅ Nova update হয়েছে: {str(res.get('from'))[:8]} → "
                    f"{str(res.get('to'))[:8]} ({len(info['commits'])}টা commit)")
        return (f"↩️ Update ব্যর্থ — নিজে আগের version-এ ফিরে গেছে "
                f"({str(res.get('from'))[:8]})।\nকোথায়: {res.get('failed_step')}\n"
                f"কারণ: {str(res.get('reason'))[:300]}"
                + ("\nDatabase backup থেকে ফেরানো হয়েছে।" if res.get("db_restored") else ""))

    async def verify(self, ctx: TaskContext, result: str) -> Verdict:
        res = read_json(self.run_dir / UPDATE_RESULT) or {}
        head = head_commit(self.app_dir)
        if res.get("task_id") != ctx.task.id:
            ok = result.startswith("✅")
            return Verdict(ok, "", VerdictStatus.PASS if ok else VerdictStatus.BLOCKED,
                           (f"version {head[:8]}",))
        if res.get("status") == "updated":
            ok = head == res.get("to")
            return Verdict(ok, "" if ok else "HEAD মেলেনি", VerdictStatus.PASS if ok else
                           VerdictStatus.BLOCKED,
                           (f"চলমান version {head[:8]} = নতুন commit {'✓' if ok else '✗'}",
                            "নতুন version-এর health check ✓", "DB backup রাখা আছে ✓"))
        back = head == res.get("from")
        return Verdict(False, "update ব্যর্থ — rollback " + ("হয়েছে" if back else "মেলেনি"),
                       VerdictStatus.BLOCKED,
                       (f"চলমান version {head[:8]} = আগের commit {'✓' if back else '✗'}",))


class LifecycleCommands:
    def __init__(self, store: TaskStore | None, engine: TaskEngine | None,
                 request_exit: RequestExit) -> None:
        self.store = store
        self.engine = engine
        self.request_exit = request_exit

    async def restart(self, msg: IncomingMessage, args: list[str]) -> str:
        if not supervised():
            return NOT_SUPERVISED
        self.request_exit(EXIT_RESTART, "owner /restart-agent")
        return ("🔄 Nova restart হচ্ছে — চলমান কাজ checkpoint-এ থামবে, ফিরে এসে আবার চলবে। "
                "এক মিনিটের মধ্যে 'আবার online' বার্তা আসবে।")

    async def update(self, msg: IncomingMessage, args: list[str]) -> str | OutgoingMessage:
        if not supervised():
            return NOT_SUPERVISED
        if self.store is None or self.engine is None:
            return "Database চালু নেই — update এখন করা যাবে না।"
        task = self.store.create(title="Nova self-update", request_text="/update",
                                 task_type=SELF_UPDATE, channel=msg.channel,
                                 chat_id=msg.chat_id)
        self.engine.wake()
        return OutgoingMessage(f"🔎 Task #{task.id}: নতুন version আছে কিনা দেখছি …",
                               task_id=task.id)
