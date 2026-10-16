"""Phase 25 live drill against the RUNNING Nova (under the supervisor). Only Nova's own
processes are touched — never the owner's apps or browser.

    uv run python scripts/stress_drill.py [--only desktop,browser,chromium,core,local]

  desktop   kill the desktop worker → the supervisor restarts it → healthy again
  browser   kill the browser worker → restarted → a browser session works
  chromium  kill the browser engine under an open session → the session reports the
            failure → a new session works
  core      kill core while a task is running → restarted → the SAME task resumes from
            its checkpoint and completes (the owner sees recovery messages on Telegram)
  local     every cloud AI fails (simulated outage, in this process) → the REAL local
            model answers; live-info questions get the honest 'no live source' answer
"""

import argparse
import asyncio
import contextlib
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config, load_secrets  # noqa: E402
from core.ipc.client import WorkerClient, WorkerError  # noqa: E402
from core.ipc.token import TokenStore  # noqa: E402

CFG = load_config()
RESULTS: list[tuple[str, bool, str]] = []
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""),
          flush=True)
    return ok


def nova_process(script: str) -> psutil.Process | None:
    """The top process running workers/<script>/run.py (the venv launcher)."""
    found = []
    for p in psutil.process_iter(["pid", "cmdline", "name"]):
        cmd = " ".join(p.info.get("cmdline") or []).replace("\\", "/")
        if f"workers/{script}/run.py" in cmd and "python" in (p.info.get("name") or "").lower():
            found.append(p)
    pids = {p.pid for p in found}
    tops = [p for p in found if p.ppid() not in pids]
    return tops[0] if tops else None


OWNER_PROFILE = r"\Google\Chrome\User Data"        # the owner's own Chrome profile


def nova_browser_processes() -> list[psutil.Process]:
    """Browser processes started by Nova's Playwright daemon ONLY. Nova uses the same
    chrome.exe as the owner — so they are found through the daemon
    (node …browser-worker…cliDaemon.js nova-…), and anything carrying the owner's
    Chrome profile is never included."""
    out: list[psutil.Process] = []
    for p in psutil.process_iter(["name", "cmdline"]):
        cmd = " ".join(p.info.get("cmdline") or [])
        if "cliDaemon.js" in cmd and "browser-worker" in cmd and " nova-" in cmd:
            for c in p.children(recursive=True):
                try:
                    ccmd = " ".join(c.cmdline())
                    if ("chrome" in c.name().lower() and OWNER_PROFILE not in ccmd
                            and "playwright" in ccmd.lower()):
                        out.append(c)
                except psutil.Error:
                    continue
    return out


def kill_tree(p: psutil.Process) -> None:
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,  # noqa: S603,S607
                   creationflags=NO_WINDOW, check=False)


def worker(name: str) -> WorkerClient:
    w = getattr(CFG.workers.workers, f"{name}_worker")
    return WorkerClient(name, w.host, w.port, TokenStore(CFG.path("secrets_dir")),
                        CFG.workers.protocol_version)


async def wait_healthy(client: WorkerClient, old_pid: int | None, limit: float = 90
                       ) -> tuple[bool, float, Any]:
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            h = await client.health()
            if h.get("pid") != old_pid:
                return True, time.time() - t0, h.get("pid")
        except WorkerError:
            pass
        await asyncio.sleep(0.5)
    return False, time.time() - t0, None


async def drill_worker(name: str, script: str) -> None:
    client = worker(name)
    try:
        before = (await client.health()).get("pid")
        proc = nova_process(script)
        if not check(f"{name}: running under the supervisor", proc is not None):
            return
        assert proc is not None
        kill_tree(proc)
        ok, took, pid = await wait_healthy(client, before)
        check(f"{name}: crashed → restarted and healthy", ok, f"{took:.1f} s, pid {pid}")
        if name == "browser" and ok:
            s = await client.call("POST", "/v1/sessions", task_id="system", json={"profile": None})
            sid = str(s["session_id"])
            closed = await client.call("DELETE", f"/v1/sessions/{sid}", task_id="system")
            check("browser: a session works after the restart", closed.get("closed") == sid)
    finally:
        await client.aclose()


async def drill_chromium() -> None:
    client = worker("browser")
    try:
        s = await client.call("POST", "/v1/sessions", task_id="system",
                              json={"profile": None, "url": "https://example.com"})
        sid = str(s["session_id"])
        check("chromium: session opened (throwaway profile)", bool(sid), s.get("url", ""))
        engines = nova_browser_processes()
        if not check("chromium: Nova's own browser processes found", bool(engines),
                     f"{len(engines)} processes under the browser worker"):
            return
        for c in engines:
            with contextlib.suppress(psutil.Error):
                c.kill()
        await asyncio.sleep(1)
        try:
            r = await client.call("POST", f"/v1/sessions/{sid}/snapshot", task_id="system")
            reported = not r.get("ok", True)
        except WorkerError as e:
            r, reported = {"error": str(e)}, True
        check("chromium: the dead session reports an error (no hang)", reported,
              str(r.get("error", ""))[:120])
        with contextlib.suppress(WorkerError):
            await client.call("DELETE", f"/v1/sessions/{sid}", task_id="system")
        s2 = await client.call("POST", "/v1/sessions", task_id="system",
                               json={"profile": None, "url": "https://example.com"})
        sid2 = str(s2["session_id"])
        snap = await client.call("POST", f"/v1/sessions/{sid2}/snapshot", task_id="system")
        check("chromium: a new session works", bool(snap.get("ok", True)) and
              "Example" in str(snap.get("snapshot", "")) + str(snap.get("title", "")))
        await client.call("DELETE", f"/v1/sessions/{sid2}", task_id="system")
    finally:
        await client.aclose()


def dashboard() -> httpx.Client:
    token = (CFG.path("secrets_dir") / "dashboard.token").read_text(encoding="utf-8").strip()
    return httpx.Client(base_url=f"http://127.0.0.1:{CFG.default.dashboard.port}",
                        headers={"Authorization": f"Bearer {token}"}, timeout=30)


def drill_core() -> None:
    d = dashboard()
    text = "C drive e koto GB jayga free ache dekho to"
    task = d.post("/api/tasks", json={"text": f"{text} (Phase 25 crash drill)"}).json()
    tid = task["id"]
    check("core: drill task created", bool(tid), f"#{tid}")
    t0 = time.time()
    state = ""
    while time.time() - t0 < 60:
        state = d.get(f"/api/tasks/{tid}").json()["state"]
        if state in ("PLANNING", "RUNNING"):
            break
        time.sleep(0.3)
    check("core: task is running", state in ("PLANNING", "RUNNING"), state)
    time.sleep(1.5)
    proc = nova_process("core-service")
    if not check("core: found", proc is not None):
        return
    assert proc is not None
    kill_tree(proc)
    killed_at = time.time()
    back = None
    while time.time() - killed_at < 120:
        try:
            d.get("/api/status").raise_for_status()
            back = time.time() - killed_at
            break
        except httpx.HTTPError:
            time.sleep(0.5)
    check("core: crashed → restarted by the supervisor", back is not None,
          f"{back:.1f} s" if back else "")
    final = {}
    while time.time() - killed_at < 300:
        final = d.get(f"/api/tasks/{tid}").json()
        if final["state"] in ("COMPLETED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    steps = [s["status"] for s in final.get("steps", [])]
    check("core: the same task resumed and COMPLETED", final.get("state") == "COMPLETED",
          f"{final.get('state')} · steps {steps} · {final.get('result_summary', '')[:80]}")
    d.close()


async def drill_local() -> None:
    from core.orchestrator.answer import NO_LIVE_BN, answer
    from models.router import ProviderRouter
    from providers.provider_base import ErrorCategory, ProviderError
    from providers.registry import ProviderRegistry
    reg = ProviderRegistry.from_config(CFG, load_secrets(CFG.path("secrets_dir")))
    for name, a in reg.adapters.items():
        if name != "ollama_local":
            async def down(request: Any, n: str = name) -> Any:
                raise ProviderError(ErrorCategory.TIMEOUT, f"{n}: no internet (drill)")
            a._complete = down  # type: ignore[method-assign]
    await asyncio.gather(*(a.check_health() for a in reg.adapters.values()))
    r = ProviderRouter(CFG.providers, reg.adapters)
    t0 = time.time()
    res = await answer(r, 0, "Explain in two sentences why the sky looks blue, step by step.",
                       "", "")
    check("local: cloud down → the real local model answers a reasoning question",
          res.result.ok and res.result.provider == "ollama_local",
          f"{res.result.provider} in {time.time() - t0:.1f} s: {res.result.answer[:80]!r}")
    live = await answer(r, 0, "আজকের বাংলাদেশের খবর কী?", "", "")
    check("local: live news without internet → honest 'no live source' (no invented news)",
          live.result.answer == NO_LIVE_BN)
    await reg.aclose()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="desktop,browser,chromium,local,core")
    which = ap.parse_args().only.split(",")
    if "desktop" in which:
        asyncio.run(drill_worker("desktop", "desktop-worker"))
    if "browser" in which:
        asyncio.run(drill_worker("browser", "browser-worker"))
    if "chromium" in which:
        asyncio.run(drill_chromium())
    if "local" in which:
        asyncio.run(drill_local())
    if "core" in which:
        drill_core()
    ok = all(r[1] for r in RESULTS)
    print(f"\nPHASE 25 LIVE DRILL: {'PASS' if ok else 'FAIL'} "
          f"({sum(r[1] for r in RESULTS)}/{len(RESULTS)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
