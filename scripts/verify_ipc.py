"""Phase 5 live check against the REAL running workers (start them first):

    uv run python workers/desktop-worker/run.py
    uv run python workers/browser-worker/run.py
    uv run python workers/privileged-broker/run.py
    uv run python scripts/verify_ipc.py
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from core.bootstrap import bootstrap  # noqa: E402
from core.ipc.client import WorkerClient, WorkerProtocolMismatch  # noqa: E402
from core.ipc.monitor import WorkerMonitor  # noqa: E402
from core.ipc.token import TokenStore  # noqa: E402
from core.log import shutdown_logging  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def main() -> int:
    ctx = bootstrap(worker="core", console_logs=False)
    w = ctx.config.workers
    tokens = TokenStore(ctx.config.path("secrets_dir"))
    v = w.protocol_version
    desk = WorkerClient("desktop", w.workers.desktop_worker.host, w.workers.desktop_worker.port,
                        tokens, v)
    brow = WorkerClient("browser", w.workers.browser_worker.host, w.workers.browser_worker.port,
                        tokens, v)
    try:
        h = await desk.health()
        check("desktop: authenticated health", h.get("worker") == "desktop", str(h))
        s = await desk.call("GET", "/v1/session", task_id=7)
        check("desktop: runs in interactive user session", bool(s["interactive_session"]),
              f"session {s['windows_session_id']}")
        created = await brow.call("POST", "/v1/sessions", task_id=7)
        sid = created["session_id"]
        check("browser: separate session id", sid != "7" and len(sid) == 32)
        closed = await brow.call("DELETE", f"/v1/sessions/{sid}", task_id=7)
        check("browser: session closed", closed.get("closed") == sid)

        base = f"http://{w.workers.desktop_worker.host}:{w.workers.desktop_worker.port}"
        good = {"Authorization": f"Bearer {tokens.current()}", "X-Request-ID": uuid.uuid4().hex,
                "X-Task-ID": "1", "X-Protocol-Version": str(v)}
        async with httpx.AsyncClient() as raw:
            bad_tok = await raw.get(f"{base}/v1/health", headers={**good,
                                    "Authorization": "Bearer " + "x" * 43})
            check("desktop: wrong token → 401", bad_tok.status_code == 401)
            no_task = {k: val for k, val in good.items() if k != "X-Task-ID"}
            r = await raw.get(f"{base}/v1/health", headers=no_task)
            check("desktop: missing Task-ID → 400", r.status_code == 400)
        try:
            await WorkerClient("desktop", w.workers.desktop_worker.host,
                               w.workers.desktop_worker.port, tokens, v + 1).health()
            check("desktop: protocol mismatch detected", False)
        except WorkerProtocolMismatch:
            check("desktop: protocol mismatch detected", True)

        tokens.rotate()
        h2 = await desk.health()
        check("token rotation picked up by running worker", h2.get("ok") is True)

        mon = WorkerMonitor([desk, brow])
        await mon.check_all()
        for name in ("desktop", "browser"):
            ok, detail = mon.probe(name)()
            check(f"/status probe: {name} worker", ok, detail)
    finally:
        await desk.aclose()
        await brow.aclose()

    if sys.platform == "win32":
        from core.ipc.pipe import pipe_call
        pipe = w.workers.privileged_broker.pipe_name
        base_msg = {"protocol_version": v, "request_id": uuid.uuid4().hex, "task_id": "system",
                    "token": tokens.current(), "params": {}}
        pong = pipe_call(pipe, {**base_msg, "action": "ping"})
        check("broker pipe: ping", pong.get("ok") is True, str(pong))
        shell = pipe_call(pipe, {**base_msg, "action": "run_shell",
                                 "params": {"cmd": "whoami"}})
        check("broker pipe: arbitrary action refused", shell.get("error") == "ACTION_NOT_ALLOWED")
        bad = pipe_call(pipe, {**base_msg, "action": "ping", "token": "y" * 43})
        check("broker pipe: wrong token refused", bad.get("error") == "UNAUTHORIZED")

        ns = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True)
        ports = {str(w.workers.desktop_worker.port), str(w.workers.browser_worker.port)}
        listening = [ln.split() for ln in ns.stdout.splitlines() if "LISTENING" in ln]
        mine = [p[1] for p in listening if p[1].rsplit(":", 1)[-1] in ports]
        check("ports bound to 127.0.0.1 only", bool(mine)
              and all(a.startswith("127.0.0.1:") for a in mine), ", ".join(mine))

    shutdown_logging()
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    passed = all(ok for _, ok, _ in results)
    print("IPC VERIFY:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
