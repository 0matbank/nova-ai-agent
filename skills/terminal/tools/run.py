"""Run a program with an argument list — never through a shell, so no
`&&`, pipes, or redirection can be smuggled in. Allowlisted read-only
invocations are terminal.read (GREEN); everything else is terminal.run (RED)."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import psutil

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult

MAX_OUTPUT = 20_000
VERSION_FLAGS = {"--version", "-v", "-V", "version"}
VERSION_PROGRAMS = {"python", "py", "node", "npm", "npx", "uv", "git", "pip", "java",
                    "ffmpeg", "ollama", "gh", "code", "dotnet"}
GIT_READ = {"status", "log", "diff", "show", "rev-parse", "ls-files", "describe", "blame",
            "shortlog"}
GIT_WRITE_FLAGS = ("--output", "-o", "--ext-diff", "--textconv")
NO_ARG_READ = {"whoami", "hostname", "systeminfo", "tasklist", "ver", "where", "ipconfig",
               "getmac", "nvidia-smi", "driverquery"}
IPCONFIG_OK = {"/all"}


class P(Params):
    program: str
    args: list[str] = []
    cwd: str | None = None
    timeout_seconds: int = 60


def _name(program: str) -> str:
    return Path(program).name.lower().removesuffix(".exe").removesuffix(".cmd")


def is_read_only(program: str, args: list[str]) -> bool:
    name = _name(program)
    if name in VERSION_PROGRAMS and len(args) == 1 and args[0] in VERSION_FLAGS:
        return True
    if name == "git" and args and args[0] in GIT_READ:
        return not any(a.startswith(GIT_WRITE_FLAGS) for a in args[1:])
    if name == "ipconfig":
        return all(a.lower() in IPCONFIG_OK for a in args)
    if name == "where":
        return len(args) == 1 and not args[0].startswith("/")
    return name in NO_ARG_READ and not args


def precheck(p: P, env: ToolEnv) -> None:
    if shutil.which(p.program) is None:
        raise PolicyDenied(f"program not found: {p.program}")
    if _name(p.program) in {"cmd", "powershell", "pwsh", "bash", "wsl", "sh"}:
        raise PolicyDenied("shells are not run via the terminal skill (use the powershell skill)")
    if env.paths.mentions_forbidden(" ".join([p.program, *p.args])):
        raise PolicyDenied("command references protected agent data (secrets/sessions/DB)")
    if p.cwd:
        env.paths.check_read(env.paths.resolve(p.cwd))
    if not 1 <= p.timeout_seconds <= 1800:
        raise PolicyDenied("timeout_seconds must be 1..1800")


def action(p: P, env: ToolEnv) -> str:
    return "terminal.read" if is_read_only(p.program, p.args) else "terminal.run"


def _kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    for child in parent.children(recursive=True):
        child.kill()
    parent.kill()


async def run(p: P, env: ToolEnv) -> ToolResult:
    exe = shutil.which(p.program)
    assert exe is not None
    cwd = str(env.paths.resolve(p.cwd)) if p.cwd else str(env.paths.workspace)
    proc = await asyncio.create_subprocess_exec(
        exe, *p.args, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=p.timeout_seconds)
    except TimeoutError:
        _kill_tree(proc.pid)
        await proc.wait()
        return ToolResult(False, f"timed out after {p.timeout_seconds}s", {"exit_code": None})
    stdout = out.decode("utf-8", errors="replace")
    stderr = err.decode("utf-8", errors="replace")
    return ToolResult(proc.returncode == 0, f"{Path(exe).name} exit {proc.returncode}",
                      {"exit_code": proc.returncode, "stdout": stdout[:MAX_OUTPUT],
                       "stderr": stderr[:MAX_OUTPUT], "program": exe},
                      {"exit_code": proc.returncode}, untrusted=True)


TOOL = Tool(name="run", params=P, run=run, action=action,
            target=lambda p: " ".join([p.program, *p.args])[:300],
            summary=lambda p: f"Terminal: {' '.join([p.program, *p.args])[:1500]}",
            precheck=precheck)
