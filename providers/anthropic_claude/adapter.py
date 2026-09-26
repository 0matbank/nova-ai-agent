"""Anthropic Claude provider (plan §8.2, Phase 14): the official Claude Code
CLI, signed in with a Claude subscription. Written now, OFF until the owner
has a subscription (`enabled: false` in providers.yaml — turning it on is only
a config change, plan §8.11).

- Subscription only (owner rule 2026-09-25): API-key variables
  (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, cloud-provider switches) are
  removed from the CLI's environment, so a run can never bill the API. Nova
  never reads or stores credentials; health = `claude auth status`.
- Print mode, prompt on stdin, `--output-format json` → one result object.
- Coding (code.edit allowed for the workspace): `--permission-mode
  acceptEdits` with only Read/Edit/Write/Glob/Grep; otherwise read-only
  (`dontAsk` with Read/Glob/Grep). Shell, web and MCP tools are always
  disallowed; Nova runs the tests and checks nothing was committed.
- Usage limit → RATE_LIMIT + cooldown, sign-in problems → AUTH.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
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

DEFAULT_COOLDOWN = 1800
API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
DISALLOWED = "Bash,WebFetch,WebSearch,mcp__*"
EDIT_TOOLS = "Read,Edit,Write,Glob,Grep"
READ_TOOLS = "Read,Glob,Grep"
_LIMIT = re.compile(r"usage limit|session limit|weekly limit|rate.?limit|\b429\b|"
                    r"limiting requests|overloaded", re.IGNORECASE)
_AUTH = re.compile(r"not logged in|log ?in|authenticat|invalid api key|\b401\b|"
                   r"oauth token", re.IGNORECASE)


def subscription_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in API_KEY_VARS}


class ClaudeAdapter(ProviderAdapter):
    name = "anthropic_claude"
    capabilities = frozenset({"coding", "review", "reasoning", "summarization", "planning"})

    def __init__(self, model: str | None = None, claude_cmd: list[str] | None = None,
                 scratch_dir: Path | None = None, disabled: bool = False) -> None:
        super().__init__()
        self.model = model
        self.cmd = claude_cmd or [shutil.which("claude") or "claude"]
        self.scratch_dir = scratch_dir
        self.disabled = disabled
        self.cooldown_until = 0.0

    def installed(self) -> bool:
        exe = self.cmd[0]
        return len(self.cmd) > 1 or Path(exe).exists() or shutil.which(exe) is not None

    async def _run(self, args: list[str], cwd: str | None, timeout: float,
                   stdin: str | None = None) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *self.cmd, *args, cwd=cwd, env=subscription_env(),
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=64 * 1024 * 1024)
        data = stdin.encode("utf-8") if stdin is not None else None
        try:
            out, err = await asyncio.wait_for(proc.communicate(data), timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise ProviderError(ErrorCategory.TIMEOUT, f"claude timed out after {timeout:.0f}s"
                                ) from None
        return (proc.returncode or 0, out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))

    # --------------------------------------------------------------- health

    async def check_health(self) -> Health:
        if self.disabled:
            return self._set(HealthState.DISABLED, "disabled in providers.yaml "
                                                   "(no Claude subscription yet)")
        if not self.installed():
            return self._set(HealthState.UNAVAILABLE, "Claude Code CLI not installed")
        left = self.cooldown_until - time.time()
        if left > 0:
            return self._set(HealthState.COOLDOWN,
                             f"usage limit — retry in {int(left // 60)} min")
        try:
            code, out, err = await self._run(["auth", "status"], None, 60)
        except (ProviderError, OSError) as e:
            return self._set(HealthState.UNAVAILABLE, f"claude auth status failed: {e}")
        if code == 0:
            return self._set(HealthState.HEALTHY, "signed in with a Claude subscription")
        return self._set(HealthState.AUTH_REQUIRED, "Claude Code not signed in — run `claude` "
                                                    f"and /login on the PC ({(err or out)[:120]})")

    # ------------------------------------------------------------- complete

    def args_for(self, request: ProviderRequest) -> list[str]:
        write = bool(request.workspace) and "code.edit" in request.allowed_tools
        args = ["-p", "--output-format", "json", "--max-turns", "40",
                "--disallowedTools", DISALLOWED]
        if write:
            args += ["--permission-mode", "acceptEdits", "--allowedTools", EDIT_TOOLS]
        else:
            args += ["--permission-mode", "dontAsk", "--allowedTools", READ_TOOLS]
        if self.model:
            args += ["--model", self.model]
        return args

    def cwd_for(self, request: ProviderRequest) -> str | None:
        if request.workspace:
            return request.workspace
        if self.scratch_dir is not None:
            self.scratch_dir.mkdir(parents=True, exist_ok=True)
            return str(self.scratch_dir)
        return None

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        if self.disabled:
            raise ProviderError(ErrorCategory.MODEL_UNAVAILABLE, "Claude is disabled in config")
        if not self.installed():
            raise ProviderError(ErrorCategory.MODEL_UNAVAILABLE, "Claude Code CLI not installed")
        t0 = time.perf_counter()
        code, out, err = await self._run(self.args_for(request), self.cwd_for(request),
                                         request.limits.timeout_seconds, stdin=_prompt(request))
        data = _last_json(out)
        is_error = bool(data.get("is_error")) or data.get("subtype") not in (None, "success")
        failure = ""
        if not data:
            failure = (err.strip() or out.strip())[-600:] or f"claude exited {code}"
        elif is_error:
            failure = str(data.get("result") or data.get("subtype") or "error")
        if failure:
            text = f"{failure}\n{err[-400:]}"
            if _LIMIT.search(text):
                wait = retry_after(text) or DEFAULT_COOLDOWN
                self.cooldown_until = time.time() + wait
                self._set(HealthState.COOLDOWN, "usage limit reached")
                raise ProviderError(ErrorCategory.RATE_LIMIT, f"claude usage limit: "
                                                              f"{failure[:200]}", retry_after=wait)
            if _AUTH.search(text):
                raise ProviderError(ErrorCategory.AUTH, f"claude sign-in needed: {failure[:200]}")
            raise ProviderError(ErrorCategory.SERVER_ERROR, f"claude failed: {failure[:300]}")
        usage = data.get("usage") or {}
        denied = data.get("permission_denials") or []
        answer = str(data.get("result") or "").strip()
        hints = (["empty answer"] if not answer else []) + (
            [f"claude refused {len(denied)} tool call(s)"] if denied else [])
        return ProviderResult(
            ResultStatus.OK, self.name, model=self.model or "claude-default", answer=answer,
            session_id=data.get("session_id"),
            usage=Usage(input_tokens=int(usage.get("input_tokens", 0) or 0),
                        output_tokens=int(usage.get("output_tokens", 0) or 0),
                        seconds=time.perf_counter() - t0),
            verification_hints=tuple(hints))


def _prompt(request: ProviderRequest) -> str:
    parts = [p for p in (request.system, request.context) if p]
    if request.previous_attempt:
        parts.append(f"Previous attempt (did not pass):\n{request.previous_attempt}")
    parts.append(request.user_request)
    if request.json_output:
        parts.append("Answer with the JSON object only.")
    return "\n\n---\n".join(parts)


def _last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    try:
        data = json.loads(stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
