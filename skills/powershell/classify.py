"""Decide whether a PowerShell command is provably read-only (GREEN
powershell.read) or not (RED powershell.run). Fail closed: any doubt → RED."""

from __future__ import annotations

import base64
import functools
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ANALYZER = Path(__file__).resolve().parent / "tools" / "analyze.ps1"

READ_VERBS = {"get", "test", "measure", "select", "where", "sort", "format", "group",
              "compare", "resolve", "split", "join", "convertto", "convertfrom"}
# ForEach-Object is deliberately absent: `... | % Delete` invokes a method via
# -MemberName without any method-call AST node.
READ_COMMANDS = {"out-string", "out-null", "write-output", "where-object"}
READ_ALIASES = {"ls", "dir", "gci", "cat", "gc", "type", "select", "where", "?",
                "sort", "ft", "fl", "fw", "measure", "echo", "write", "gps", "ps",
                "gsv", "pwd", "gl", "gi", "gp", "gcm", "gm", "group", "compare", "gwmi",
                "gcim", "gdr", "ghy", "h", "history"}
# Get-* commands that change state or pop UI despite the verb.
DENY_ANYWAY = {"get-credential"}


@dataclass(frozen=True)
class Verdict:
    read_only: bool
    reason: str


def _powershell() -> str:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if exe is None:
        raise RuntimeError("PowerShell not found")
    return exe


def _allowed(name: str) -> bool:
    n = name.lower()
    if n in DENY_ANYWAY:
        return False
    if n in READ_COMMANDS or n in READ_ALIASES:
        return True
    verb, _, noun = n.partition("-")
    return bool(noun) and verb in READ_VERBS


@functools.lru_cache(maxsize=256)
def analyze(command: str) -> Verdict:
    b64 = base64.b64encode(command.encode("utf-8")).decode()
    try:
        proc = subprocess.run(  # noqa: S603 - fixed exe, analyzer script, no shell
            [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(ANALYZER), "-B64", b64],
            capture_output=True, text=True, timeout=20, check=False)
        info = json.loads(proc.stdout.strip() or "{}")
    except (subprocess.TimeoutExpired, ValueError, RuntimeError, OSError) as e:
        return Verdict(False, f"analysis failed ({type(e).__name__})")
    if not info:
        return Verdict(False, "analysis produced no result")
    if info.get("parse_errors"):
        return Verdict(False, f"parse error: {info['parse_errors'][0]}")
    commands = info.get("commands") or []
    if isinstance(commands, dict):
        commands = [commands]
    if not commands:
        return Verdict(False, "no command found")
    for c in commands:
        name = c.get("name")
        if not name:
            return Verdict(False, "dynamic command name")
        if c.get("op") not in (None, "Unknown"):
            return Verdict(False, f"invocation operator on {name}")
        if not _allowed(name):
            return Verdict(False, f"{name} is not a read-only command")
    redirs = info.get("redirections") or []
    if isinstance(redirs, str):
        redirs = [redirs]
    if any(r.strip().lower() != "$null" for r in redirs):
        return Verdict(False, "writes output to a file")
    if info.get("member_invocations"):
        return Verdict(False, ".NET method call")
    if info.get("unsafe_assignments"):
        return Verdict(False, "assigns to a property or provider path")
    if info.get("script_invocations"):
        return Verdict(False, "script block invocation")
    return Verdict(True, "read-only: " + ", ".join(sorted({c["name"] for c in commands})))
