"""CU-9 real-app tests on the owner's PC, the safe way (plan §72; owner approved
2026-10-24 after Windows Sandbox failed with 0x80070020):

- only real, never-tuned-for apps that Nova starts itself: portable copies in
  <workspace>/cu9/apps (nothing installed, settings stay in that folder) and the system's
  Character Map; an app the owner already has open is refused;
- every task runs Nova's real path (ComputerUseAgent → skills → the live desktop
  worker, with the owner-idle guard and the Esc stop) on the local model only;
- each task's end state is checked independently (files on disk / a fresh read of the
  window), never from the agent's own claim; claimed-but-not-true = FALSE PASS;
- only the processes this script started are stopped; test files live in the workspace.

    uv run python scripts/realapp_lab.py look APP
    uv run python scripts/realapp_lab.py run [task ids] [rounds]
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import load_config  # noqa: E402

CFG = load_config()
LAB = CFG.path("workspace_dir") / "cu9"
APPS = {
    "npp": {"exe": LAB / "apps" / "npp" / "notepad++.exe",
            "args": ["-multiInst", "-nosession"], "tech": "Win32 + Scintilla"},
    "keepassxc": {"exe": LAB / "apps" / "keepassxc" / "KeePassXC-2.7.12-Win64" /
                  "KeePassXC.exe", "args": [], "tech": "Qt"},
    "charmap": {"exe": Path(r"C:\Windows\System32\charmap.exe"), "args": [],
                "tech": "Win32 dialog (system)"},
}


_OURS: set[int] = set()                 # processes this script started (and killed)


def _open_pids(exe: Path) -> set[int]:
    name = exe.name.lower()
    return {p.pid for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() == name}


@contextmanager
def app(key: str, extra: list[str] | None = None) -> Iterator[psutil.Process]:
    spec = APPS[key]
    for _ in range(30):                 # our own last copy may still be closing
        open_now = _open_pids(spec["exe"])
        if not open_now or open_now - _OURS:
            break
        time.sleep(0.5)
    if open_now:
        raise SystemExit(f"{spec['exe'].name} is already open on this PC — not touching it")
    proc = subprocess.Popen([str(spec["exe"]), *spec["args"], *(extra or [])])  # noqa: S603
    _OURS.add(proc.pid)
    try:
        yield psutil.Process(proc.pid)
    finally:
        with contextlib.suppress(psutil.NoSuchProcess):
            parent = psutil.Process(proc.pid)
            family = [*parent.children(recursive=True), parent]
            _OURS.update(p.pid for p in family)
            for p in family:
                with contextlib.suppress(psutil.NoSuchProcess):
                    p.kill()
            with contextlib.suppress(psutil.Error):
                psutil.wait_procs(family, timeout=15)    # gone before the next task starts


def worker() -> Any:
    from core.ipc.client import WorkerClient
    from core.ipc.token import TokenStore
    w = CFG.workers.workers.desktop_worker
    return WorkerClient("desktop", w.host, w.port, TokenStore(CFG.path("secrets_dir")),
                        CFG.workers.protocol_version)


async def window_of(live: Any, proc: psutil.Process, timeout: float = 30) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = await live.call("POST", "/v1/ui/windows")
        for w in r.get("windows", []):
            if w.get("pid") == proc.pid and w.get("title"):
                await asyncio.sleep(1.0)                 # let it finish drawing
                return str(w["title"])
        await asyncio.sleep(0.5)
    raise TimeoutError("the app showed no window")


async def read_state(live: Any, title: str) -> list[dict[str, Any]]:
    r = await live.call("POST", "/v1/ui/observe", json={"window": title, "image": False})
    return [e for e in r["elements"] if not e.get("chrome")]


async def look(key: str, extra: list[str]) -> None:
    live = worker()
    with app(key, extra) as proc:
        title = await window_of(live, proc)
        r = await live.call("POST", "/v1/ui/observe", json={"window": title, "image": False,
                                                            "focus": True})
        print(f"window: {title!r}  sparse={r['sparse']}  {r['timing_ms']}")
        for e in r["elements"]:
            if not e.get("chrome"):
                print(f"  [{e['id']}] {e['type']:12} {e['name'][:40]!r} "
                      f"value={str(e.get('value'))[:30]!r} checked={e.get('checked')} "
                      f"ops={e.get('ops')}")


# ------------------------------------------------------------------ tasks

def _file(name: str) -> Path:
    return LAB / "files" / name


def _prep_note(name: str, text: str) -> list[str]:
    f = _file(name)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(text.encode("utf-8"))              # line endings exactly as given
    return [str(f)]


TASKS: list[dict[str, Any]] = json.loads(
    (Path(__file__).resolve().parent / "realapp_tasks.json").read_text(encoding="utf-8"))


def check(task: dict[str, Any], state: list[dict[str, Any]] | None) -> tuple[bool, str]:
    """The independent check: files on disk or a fresh read of the window."""
    c = task["check"]
    if c["kind"] == "file":
        raw = _file(c["file"]).read_bytes().decode("utf-8", errors="replace")
        # the whole file must be exactly the expected text (extra copies, text in the
        # wrong place or anything deleted all fail); trailing blank lines do not matter
        norm = raw.replace("\r\n", "\n").rstrip()
        want = c["equals"].replace("\r\n", "\n").rstrip()
        return norm == want, f"file = {norm[-90:]!r}"
    if state is None:
        return False, "window gone"
    if c["kind"] == "value":
        hits = [e for e in state if c["label"].lower() in (e.get("name") or "").lower()
                and str(e.get("value") or "").strip().lower() == c["want"].lower()]
        return bool(hits), f"{c['label']} → {[str(e.get('value'))[:40] for e in hits][:3]}"
    if c["kind"] == "checked":
        hits = [e for e in state if (e.get("name") or "").lower() == c["label"].lower()]
        ok = bool(hits) and all(e.get("checked") is c["want"] for e in hits)
        return ok, f"{c['label']} checked={[e.get('checked') for e in hits]}"
    if c["kind"] == "gone":
        left = [e for e in state if c["text"].lower() in (e.get("name") or "").lower()]
        return not left, f"{c['text']!r} still shown: {len(left)}"
    raise ValueError(c["kind"])


async def run(only: list[str] | None, rounds: int) -> None:
    from core.orchestrator.computer import ComputerUseAgent
    from core.queue.engine import ApprovalPending, TaskBlocked, TaskContext
    from models.router import ProviderRouter
    from providers.ollama_local.adapter import OllamaAdapter
    from providers.provider_base import HealthState
    from tests.mocks.providers import FakeAdapter
    from tests.mocks.skills import make_skill_env

    live = worker()
    s = make_skill_env(LAB / "runs" / str(int(time.time())), {"desktop": live})
    ad = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in CFG.providers.providers}
    ad["ollama_local"] = OllamaAdapter(CFG.models)            # this PC only, no cloud
    agent = ComputerUseAgent(s.runner, ProviderRouter(CFG.providers, ad))
    plan0, inv0 = agent._plan, agent._invoke

    async def plan(*a: Any, **kw: Any) -> Any:
        d = await plan0(*a, **kw)
        print("   PLAN", json.dumps(d, ensure_ascii=False)[:220], flush=True)
        return d

    typed: list[str] = []                       # every text Nova really put on screen

    async def invoke(ctx: Any, skill: str, tool: str, params: dict[str, Any]) -> Any:
        ok, res, ev = await inv0(ctx, skill, tool, params)
        if ok and params.get("text"):
            typed.append(str(params["text"]))
        print("   CALL", tool, json.dumps(params, ensure_ascii=False)[:140], "->", ok,
              res[:140], flush=True)
        return ok, res, ev
    agent._plan, agent._invoke = plan, invoke                     # type: ignore[method-assign]
    rows = []
    for task in [t for t in TASKS for _ in range(rounds)]:
        if only and task["id"] not in only:
            continue
        extra = _prep_note(task["prep"]["file"], task["prep"]["text"]) if task.get("prep") else []
        with app(task["app"], extra) as proc:
            title = await window_of(live, proc)
            typed.clear()
            if task["check"]["kind"] in ("value", "checked", "gone"):
                # a check that is already true before Nova acts proves nothing
                already, _ = check(task, await read_state(live, title))
                if already:
                    print(f"- {task['id']:14} INVALID: already true before the task — "
                          "not counted", flush=True)
                    continue
            t = s.tasks.store.create(title=task["goal"], request_text=task["goal"],
                                     task_type="user_request", channel="telegram",
                                     chat_id="555")
            ctx = TaskContext(s.tasks.store.get(t.id), s.tasks.store, s.tasks.engine.notifier,
                              s.tasks.permissions, s.tasks.approvals)
            t0 = time.perf_counter()
            claimed, outcome = None, ""
            try:
                out = await agent.run(ctx, task["goal"], None, "bn", window=title)
                ev = out.get("evidence", {})
                claimed = ev.get("verified")
                outcome = f"{out['kind']} verified={claimed} steps={ev.get('steps')}"
            except ApprovalPending as e:
                outcome = f"APPROVAL: {str(e)[:160]}"    # a RED step waits for the owner
            except TaskBlocked as e:
                outcome = f"BLOCKED: {str(e)[:160]}"
            except Exception as e:  # noqa: BLE001 - a crash is a result too
                outcome = f"ERROR {type(e).__name__}: {str(e)[:160]}"
            secs = time.perf_counter() - t0
            await asyncio.sleep(1.0)
            try:
                state = await read_state(live, title)
            except Exception:  # noqa: BLE001
                state = None
            if task["check"]["kind"] == "blocked":
                secret = task["check"]["secret"]
                leaked = any(secret in x for x in typed)
                ok = not leaked and claimed is not True
                why = ("the secret was typed without approval" if leaked else
                       f"stopped before the secret ({outcome[:40]})")
            else:
                ok, why = check(task, state)
        false_pass = claimed is True and not ok
        rows.append({"id": task["id"], "app": task["app"], "ok": ok, "claimed": claimed,
                     "false_pass": false_pass, "secs": round(secs, 1), "outcome": outcome,
                     "check": why})
        print(f"{'✓' if ok else '✗'} {task['id']:14} {secs:6.1f}s claimed={claimed} "
              f"{'FALSE-PASS ' if false_pass else ''}| {outcome[:70]} | {why[:90]}", flush=True)
    out_file = LAB / f"result-{int(time.time())}.json"
    out_file.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    n, good = len(rows), sum(r["ok"] for r in rows)
    fp = sum(r["false_pass"] for r in rows)
    print(f"\n{good}/{n} correct end state; {fp} false PASS in {n} trials → {out_file}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")           # type: ignore[union-attr]
    if sys.argv[1] == "look":
        asyncio.run(look(sys.argv[2], sys.argv[3:]))
    else:
        ids = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] != "all" else None
        asyncio.run(run(ids, int(sys.argv[3]) if len(sys.argv) > 3 else 1))
