"""Run a PowerShell command as the current (non-elevated) user.
Read-only (parser-verified) → powershell.read GREEN; anything else →
powershell.run RED with the exact command shown for approval."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import sys
from pathlib import Path

import psutil

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult

MAX_OUTPUT = 20_000
PREAMBLE = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "$ProgressPreference='SilentlyContinue';")


def _classifier():  # type: ignore[no-untyped-def]
    name = "nova_skill_powershell_classify"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "classify.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class P(Params):
    command: str
    timeout_seconds: int = 60
    cwd: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    if not p.command.strip():
        raise PolicyDenied("empty command")
    if env.paths.mentions_forbidden(p.command):
        raise PolicyDenied("command references protected agent data (secrets/sessions/DB)")
    if p.cwd:
        env.paths.check_read(env.paths.resolve(p.cwd))
    if not 1 <= p.timeout_seconds <= 600:
        raise PolicyDenied("timeout_seconds must be 1..600")


def action(p: P, env: ToolEnv) -> str:
    return "powershell.read" if _classifier().analyze(p.command).read_only else "powershell.run"


def summary(p: P) -> str:
    v = _classifier().analyze(p.command)
    return f"PowerShell command:\n{p.command[:1500]}\n\nClassifier: {v.reason}"


def _kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    for child in parent.children(recursive=True):
        child.kill()
    parent.kill()


async def run(p: P, env: ToolEnv) -> ToolResult:
    exe = _classifier()._powershell()
    encoded = base64.b64encode((PREAMBLE + p.command).encode("utf-16-le")).decode()
    cwd = str(env.paths.resolve(p.cwd)) if p.cwd else str(env.paths.workspace)
    proc = await asyncio.create_subprocess_exec(
        exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", encoded, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=p.timeout_seconds)
    except TimeoutError:
        _kill_tree(proc.pid)
        await proc.wait()
        return ToolResult(False, f"timed out after {p.timeout_seconds}s", {"exit_code": None})
    stdout = out.decode("utf-8", errors="replace")
    stderr = err.decode("utf-8", errors="replace")
    ok = proc.returncode == 0
    return ToolResult(ok, f"exit {proc.returncode}, {len(stdout)} chars of output",
                      {"exit_code": proc.returncode, "stdout": stdout[:MAX_OUTPUT],
                       "stderr": stderr[:MAX_OUTPUT], "truncated": len(stdout) > MAX_OUTPUT},
                      {"exit_code": proc.returncode}, untrusted=True)


TOOL = Tool(name="run", params=P, run=run, action=action, target=lambda p: p.command[:300],
            summary=summary, precheck=precheck)
