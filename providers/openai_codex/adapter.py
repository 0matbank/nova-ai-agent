"""OpenAI Codex provider (plan §8.2, Phase 12): the official Codex CLI, signed
in with the owner's ChatGPT account ("Sign in with ChatGPT"), so the ChatGPT
plan's usage is used — not OpenAI API billing.

- Credentials stay in the CLI's own store (~/.codex); Nova never reads or
  keeps them (owner rule 2026-09-25). Health = `codex login status`.
- Non-interactive: `codex exec --json`, one JSONL event per line, parsed into
  the common ProviderResult (answer, files changed, commands, session id, usage).
- Sandbox: read-only unless the request explicitly allows "code.edit" for its
  workspace — then workspace-write with NO network. The core authorises that
  before calling (Coding flow, permission `code.edit`); Codex never pushes.
- Usage limit / 429 → RATE_LIMIT + cooldown (retry-after when the CLI says
  when), so the router moves on to the next provider (plan §8.5, §8.7).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from providers.provider_base import (
    ErrorCategory,
    Health,
    HealthState,
    ProviderAdapter,
    ProviderError,
    ProviderRequest,
    ProviderResult,
    ResultStatus,
    Usage,
)
from providers.provider_base.adapter import retry_after

__all__ = ["CodexAdapter", "parse_events", "retry_after"]

HERE = Path(__file__).resolve().parent
CODEX_JS = HERE / "cli" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
HEALTH_TTL = 300
DEFAULT_COOLDOWN = 1800
_LIMIT = re.compile(r"usage limit|rate limit|too many requests|\b429\b|quota", re.IGNORECASE)
_AUTH = re.compile(r"not logged in|log ?in again|unauthori[sz]ed|\b401\b|token.*expired|"
                   r"refresh token", re.IGNORECASE)


def _node() -> str:
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    return node


class CodexAdapter(ProviderAdapter):
    name = "openai_codex"
    capabilities = frozenset({"coding", "review", "reasoning"})

    def __init__(self, model: str | None = None, codex_cmd: list[str] | None = None) -> None:
        super().__init__()
        self.model = model
        self.cmd = codex_cmd or [_node(), str(CODEX_JS)]
        self.cooldown_until = 0.0

    def installed(self) -> bool:
        return len(self.cmd) != 2 or Path(self.cmd[1]).exists()

    async def _run(self, args: list[str], cwd: str | None, timeout: float
                   ) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *self.cmd, *args, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=64 * 1024 * 1024)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ProviderError(ErrorCategory.TIMEOUT, f"codex timed out after {timeout:.0f}s"
                                ) from None
        return (proc.returncode or 0, out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))

    # --------------------------------------------------------------- health

    async def check_health(self) -> Health:
        if not self.installed():
            return self._set(HealthState.UNAVAILABLE, "Codex CLI not installed "
                                                      "(npm ci in providers/openai_codex/cli)")
        left = self.cooldown_until - time.time()
        if left > 0:
            return self._set(HealthState.COOLDOWN, f"usage limit — retry in {int(left // 60)} min")
        try:
            code, out, err = await self._run(["login", "status"], None, 60)
        except (ProviderError, OSError) as e:
            return self._set(HealthState.UNAVAILABLE, f"codex login status failed: {e}")
        text = f"{out}\n{err}"
        if "logged in using chatgpt" in text.lower():
            return self._set(HealthState.HEALTHY, "signed in with ChatGPT (plan usage)")
        if "logged in" in text.lower() and "not" not in text.lower():
            # An API-key login would bill the OpenAI API — not what the owner chose.
            return self._set(HealthState.AUTH_REQUIRED,
                             "Codex is signed in with an API key, not ChatGPT — run "
                             "`codex login` and choose Sign in with ChatGPT")
        return self._set(HealthState.AUTH_REQUIRED, "Codex not signed in — run `codex login` "
                                                    "(Sign in with ChatGPT) on the PC")

    # ------------------------------------------------------------- complete

    def args_for(self, request: ProviderRequest) -> list[str]:
        write = bool(request.workspace) and "code.edit" in request.allowed_tools
        args = ["exec", "--json", "--color", "never",
                "--sandbox", "workspace-write" if write else "read-only",
                "-c", "sandbox_workspace_write.network_access=false",
                "-c", 'approval_policy="never"']
        if request.workspace:
            args += ["-C", request.workspace]
        else:
            args += ["--skip-git-repo-check"]
        if self.model:
            args += ["-m", self.model]
        return [*args, "--", _prompt(request)]

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        if not self.installed():
            raise ProviderError(ErrorCategory.MODEL_UNAVAILABLE, "Codex CLI not installed")
        t0 = time.perf_counter()
        code, out, err = await self._run(self.args_for(request), request.workspace,
                                         request.limits.timeout_seconds)
        parsed = parse_events(out)
        failure = parsed["error"] or ("" if code == 0 else (err.strip()[-600:] or
                                                            f"codex exited {code}"))
        if failure:
            text = f"{failure}\n{err[-600:]}"
            if _LIMIT.search(text):
                wait = retry_after(text) or DEFAULT_COOLDOWN
                self.cooldown_until = time.time() + wait
                self._set(HealthState.COOLDOWN, "usage limit reached")
                raise ProviderError(ErrorCategory.RATE_LIMIT, f"codex usage limit: {failure[:200]}",
                                    retry_after=wait)
            if _AUTH.search(text):
                raise ProviderError(ErrorCategory.AUTH, f"codex sign-in needed: {failure[:200]}")
            raise ProviderError(ErrorCategory.SERVER_ERROR, f"codex failed: {failure[:300]}")
        usage = parsed["usage"]
        return ProviderResult(
            ResultStatus.OK, self.name, model=self.model or "codex-default",
            answer=parsed["answer"], files_changed=tuple(parsed["files"]),
            commands=tuple(parsed["commands"]), tool_events=tuple(parsed["events"]),
            session_id=parsed["session_id"],
            usage=Usage(input_tokens=int(usage.get("input_tokens", 0)),
                        output_tokens=int(usage.get("output_tokens", 0)),
                        seconds=time.perf_counter() - t0),
            verification_hints=("empty answer",) if not parsed["answer"] else ())


def _prompt(request: ProviderRequest) -> str:
    parts = [p for p in (request.system, request.context) if p]
    if request.previous_attempt:
        parts.append(f"Previous attempt (did not pass):\n{request.previous_attempt}")
    parts.append(request.user_request)
    return "\n\n---\n".join(parts)


def parse_events(stdout: str) -> dict[str, Any]:
    """Codex `exec --json` JSONL → answer / files / commands / session / usage / error."""
    out: dict[str, Any] = {"answer": "", "files": [], "commands": [], "events": [],
                           "session_id": None, "usage": {}, "error": ""}
    messages: list[str] = []
    for line in stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        kind = str(e.get("type", ""))
        if kind == "thread.started":
            out["session_id"] = e.get("thread_id")
        elif kind == "turn.completed":
            out["usage"] = e.get("usage") or {}
        elif kind in ("turn.failed", "error"):
            raw = e.get("error")
            err: dict[str, Any] = raw if isinstance(raw, dict) else {}
            out["error"] = str(err.get("message") or e.get("message") or raw or kind)
        elif kind == "item.completed":
            item = e.get("item") or {}
            itype = item.get("type")
            if itype == "agent_message":
                messages.append(str(item.get("text", "")))
            elif itype == "command_execution":
                out["commands"].append(str(item.get("command", ""))[:300])
                out["events"].append({"type": "command", "exit_code": item.get("exit_code"),
                                      "command": str(item.get("command", ""))[:300]})
            elif itype == "file_change":
                for ch in item.get("changes") or []:
                    path = str(ch.get("path", ""))
                    if path and path not in out["files"]:
                        out["files"].append(path)
                out["events"].append({"type": "file_change", "changes": item.get("changes")})
            elif itype == "error":
                out["events"].append({"type": "error", "message": item.get("message")})
    out["answer"] = messages[-1].strip() if messages else ""
    return out
