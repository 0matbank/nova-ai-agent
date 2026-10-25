"""Run a PowerShell command as the current (non-elevated) user.
Read-only (parser-verified) → powershell.read GREEN; anything else →
powershell.run RED with the exact command shown for approval."""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.proc import clean_env, run_contained

MAX_OUTPUT = 20_000
PREAMBLE = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "$ProgressPreference='SilentlyContinue';")
# V1.2 hardening: the command travels in this environment variable, not on the command
# line (no process list shows it, no 32k limit); PowerShell clears it before running it,
# so nothing the command starts can read it back
COMMAND_VAR = "NOVA_PS_COMMAND"
LOADER = (f"$nova_c=[Environment]::GetEnvironmentVariable('{COMMAND_VAR}');"
          f"[Environment]::SetEnvironmentVariable('{COMMAND_VAR}',$null);"
          "& ([ScriptBlock]::Create($nova_c))")
# -Command exits 1 when the last command failed; a script block alone exits 0 — this
# line keeps that meaning (measured: same exit codes as before for failures, exit N,
# native failures, throw)
EXIT_TAIL = "\nif (-not $?) { exit 1 }"


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


async def run(p: P, env: ToolEnv) -> ToolResult:
    exe = _classifier()._powershell()
    loader = base64.b64encode(LOADER.encode("utf-16-le")).decode()
    cwd = str(env.paths.resolve(p.cwd)) if p.cwd else str(env.paths.workspace)
    done = await run_contained(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", loader], cwd=cwd, timeout=p.timeout_seconds,
        env=clean_env({COMMAND_VAR: PREAMBLE + p.command + EXIT_TAIL}),
        max_output=MAX_OUTPUT)
    stdout = done.stdout.decode("utf-8", errors="replace")
    stderr = done.stderr.decode("utf-8", errors="replace")
    if done.timed_out:
        return ToolResult(False, f"timed out after {p.timeout_seconds}s — every process it "
                                 "started was ended",
                          {"exit_code": None, "stdout": stdout, "stderr": stderr},
                          untrusted=True)
    ok = done.exit_code == 0
    return ToolResult(ok, f"exit {done.exit_code}, {len(stdout)} chars of output",
                      {"exit_code": done.exit_code, "stdout": stdout, "stderr": stderr,
                       "truncated": done.truncated},
                      {"exit_code": done.exit_code}, untrusted=True)


TOOL = Tool(name="run", params=P, run=run, action=action, target=lambda p: p.command[:300],
            summary=summary, precheck=precheck)
