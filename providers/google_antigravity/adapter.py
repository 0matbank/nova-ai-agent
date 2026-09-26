"""Google Antigravity provider (plan §8.2, Phase 13): Google's official
Antigravity CLI (`agy`), signed in with the owner's Google account, so the
Google AI Pro plan's quota is used — not Gemini API billing.

- Credentials stay in the CLI's own store (the system keyring); Nova never
  reads or keeps them (owner rule 2026-09-25). Health = `agy models`, which
  answers in about a second, costs no quota and says "Please sign in" when
  there is no session.
- Non-interactive print mode with NDJSON in and out (`--input-format
  stream-json --output-format stream-json`): the prompt goes in on stdin (no
  command-line length or quoting limits), the `result` envelope comes back
  with the answer, status, conversation id and usage.
- Always `--sandbox` (terminal commands only see the workspace, no network).
  File edits only when the request explicitly allows "code.edit" for its
  workspace — then `--mode accept-edits`; otherwise edits are soft-denied, as
  print mode does for anything needing approval. Never
  `--dangerously-skip-permissions`. The core authorises code.edit first
  (Coding flow) and checks that nothing was committed.
- Without a workspace the agent runs in an empty scratch folder, so it has
  nothing of the owner's to look at.
- Quota / 429 → RATE_LIMIT + cooldown, sign-in problems → AUTH, so the router
  moves on to the next provider (plan §8.5, §8.7).
"""

from __future__ import annotations

import asyncio
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
_LIMIT = re.compile(r"quota|rate.?limit|resource.?exhausted|too many requests|\b429\b|"
                    r"usage limit|out of credits|credits? (?:exhausted|remaining: 0)",
                    re.IGNORECASE)
_AUTH = re.compile(r"authenticat|sign ?in|log ?in|unauthori[sz]ed|\b401\b|token.*expired|"
                   r"permission denied.*account", re.IGNORECASE)
# CLI start-up noise on stderr that is never the reason for a failure
_NOISE = re.compile(r"logging before google\.Init|oauth2/auth\?|^\s*$")
_EDIT_TOOLS = re.compile(r"write|edit|replace|create_file|patch", re.IGNORECASE)
_COMMAND_TOOLS = re.compile(r"command|terminal|shell|exec", re.IGNORECASE)


def default_cli() -> str:
    found = shutil.which("agy")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    return str(Path(local) / "agy" / "bin" / "agy.exe") if local else "agy"


class AntigravityAdapter(ProviderAdapter):
    name = "google_antigravity"
    capabilities = frozenset({"coding", "review", "reasoning", "summarization"})

    def __init__(self, model: str | None = None, agy_cmd: list[str] | None = None,
                 scratch_dir: Path | None = None) -> None:
        super().__init__()
        self.model = model
        self.cmd = agy_cmd or [default_cli()]
        self.scratch_dir = scratch_dir
        self.cooldown_until = 0.0

    def installed(self) -> bool:
        exe = self.cmd[0]
        return len(self.cmd) > 1 or Path(exe).exists() or shutil.which(exe) is not None

    async def _run(self, args: list[str], cwd: str | None, timeout: float,
                   stdin: str | None = None) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *self.cmd, *args, cwd=cwd,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=64 * 1024 * 1024)
        data = stdin.encode("utf-8") if stdin is not None else None
        try:
            out, err = await asyncio.wait_for(proc.communicate(data), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ProviderError(ErrorCategory.TIMEOUT, f"agy timed out after {timeout:.0f}s"
                                ) from None
        return (proc.returncode or 0, out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))

    # --------------------------------------------------------------- health

    async def check_health(self) -> Health:
        if not self.installed():
            return self._set(HealthState.UNAVAILABLE, "Antigravity CLI (agy) not installed")
        left = self.cooldown_until - time.time()
        if left > 0:
            return self._set(HealthState.COOLDOWN,
                             f"quota used up — retry in {int(left // 60)} min")
        try:
            code, out, err = await self._run(["models"], None, 60)
        except (ProviderError, OSError) as e:
            return self._set(HealthState.UNAVAILABLE, f"agy models failed: {e}")
        text = f"{out}\n{err}"
        if code == 0 and "sign in" not in text.lower():
            return self._set(HealthState.HEALTHY, "signed in with Google (AI Pro quota)")
        if _AUTH.search(text):
            return self._set(HealthState.AUTH_REQUIRED, "Antigravity not signed in — run `agy` "
                                                        "once on the PC and sign in with Google")
        return self._set(HealthState.UNAVAILABLE, f"agy models failed: {_reason(err, out)}")

    # ------------------------------------------------------------- complete

    def args_for(self, request: ProviderRequest) -> list[str]:
        write = bool(request.workspace) and "code.edit" in request.allowed_tools
        args = ["--print", "--input-format", "stream-json", "--output-format", "stream-json",
                "--print-timeout", f"{int(request.limits.timeout_seconds)}s",
                "--disable-slash-commands", "--sandbox"]
        if write:
            args += ["--mode", "accept-edits"]
        if self.model:
            args += ["--model", self.model]
        if request.task_type == "summarization":
            args += ["--effort", "low"]
        return args

    def cwd_for(self, request: ProviderRequest) -> str | None:
        if request.workspace:
            return request.workspace
        if self.scratch_dir is not None:
            self.scratch_dir.mkdir(parents=True, exist_ok=True)
            return str(self.scratch_dir)
        return None

    async def _complete(self, request: ProviderRequest) -> ProviderResult:
        if not self.installed():
            raise ProviderError(ErrorCategory.MODEL_UNAVAILABLE, "Antigravity CLI not installed")
        t0 = time.perf_counter()
        message = json.dumps({"event": "user", "message": {"content": _prompt(request)}},
                             ensure_ascii=False) + "\n"
        # a little longer than the CLI's own --print-timeout, so its clean error wins
        code, out, err = await self._run(self.args_for(request), self.cwd_for(request),
                                         request.limits.timeout_seconds + 30, stdin=message)
        parsed = parse_stream(out)
        status = str(parsed["status"] or "").upper()
        failure = parsed["error"] or ("" if status in ("SUCCESS", "") and code == 0 else
                                      (status if status and status != "SUCCESS" else "")
                                      or _reason(err, out) or f"agy exited {code}")
        if not failure and not parsed["result_seen"]:
            failure = _reason(err, out) or "agy returned no result"
        if failure:
            text = f"{failure}\n{_reason(err, '')}"
            if _LIMIT.search(text):
                wait = retry_after(text) or DEFAULT_COOLDOWN
                self.cooldown_until = time.time() + wait
                self._set(HealthState.COOLDOWN, "quota used up")
                raise ProviderError(ErrorCategory.RATE_LIMIT, f"agy quota: {failure[:200]}",
                                    retry_after=wait)
            if _AUTH.search(text):
                raise ProviderError(ErrorCategory.AUTH, f"agy sign-in needed: {failure[:200]}")
            if "timed out" in text.lower() or status == "INTERRUPTED":
                raise ProviderError(ErrorCategory.TIMEOUT, f"agy: {failure[:200]}")
            raise ProviderError(ErrorCategory.SERVER_ERROR, f"agy failed: {failure[:300]}")
        usage = parsed["usage"]
        return ProviderResult(
            ResultStatus.OK, self.name, model=parsed["model"] or self.model or "agy-default",
            answer=parsed["answer"], files_changed=tuple(parsed["files"]),
            commands=tuple(parsed["commands"]), tool_events=tuple(parsed["events"]),
            session_id=parsed["session_id"],
            usage=Usage(input_tokens=int(usage.get("input_tokens", 0) or 0),
                        output_tokens=int(usage.get("output_tokens", 0) or 0),
                        seconds=time.perf_counter() - t0),
            verification_hints=("empty answer",) if not parsed["answer"] else ())


def _prompt(request: ProviderRequest) -> str:
    parts = [p for p in (request.system, request.context) if p]
    if request.previous_attempt:
        parts.append(f"Previous attempt (did not pass):\n{request.previous_attempt}")
    parts.append(request.user_request)
    if request.json_output:
        parts.append("Answer with the JSON object only.")
    return "\n\n---\n".join(parts)


def _reason(err: str, out: str) -> str:
    rows = [ln.strip() for ln in f"{err}\n{out}".splitlines()
            if not _NOISE.search(ln) and not ln.lstrip().startswith("{")]
    return " ".join(rows)[-600:]


def _kind(e: dict[str, Any]) -> str:
    return str(e.get("type") or e.get("event") or "")


def parse_stream(stdout: str) -> dict[str, Any]:
    """`agy --output-format stream-json|json` → answer / status / error / files /
    commands / conversation id / usage. Unknown events and lines are ignored."""
    out: dict[str, Any] = {"answer": "", "status": "", "error": "", "files": [],
                           "commands": [], "events": [], "session_id": None, "usage": {},
                           "model": None, "result_seen": False}
    deltas: list[str] = []
    for line in stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        kind = _kind(e)
        if kind == "init":
            out["session_id"] = e.get("conversation_id") or out["session_id"]
            out["model"] = e.get("model") or out["model"]
        elif kind == "step_update":
            if e.get("text_delta"):
                deltas.append(str(e["text_delta"]))
            tool = str(e.get("tool_name") or "")
            if tool and str(e.get("state", "")).upper() == "DONE":
                _tool_step(out, tool, e.get("tool_info"))
        elif kind == "result" or ("status" in e and "response" in e):
            out["result_seen"] = True
            out["status"] = str(e.get("status") or "")
            out["error"] = str(e.get("error") or "")
            out["answer"] = str(e.get("response") or "").strip()
            out["session_id"] = e.get("conversation_id") or out["session_id"]
            out["usage"] = e.get("usage") or {}
    if not out["answer"] and deltas and not out["error"]:
        out["answer"] = "".join(deltas).strip()
    return out


def _tool_step(out: dict[str, Any], tool: str, info: Any) -> None:
    params: dict[str, Any] = {}
    if isinstance(info, dict):
        raw = info.get("parameters") or info.get("params") or info.get("input") or {}
        params = raw if isinstance(raw, dict) else {}
    if _COMMAND_TOOLS.search(tool):
        cmd = str(params.get("command") or params.get("CommandLine") or params.get("cmd") or "")
        if cmd:
            out["commands"].append(cmd[:300])
        out["events"].append({"type": "command", "tool": tool, "command": cmd[:300]})
    elif _EDIT_TOOLS.search(tool):
        path = str(params.get("path") or params.get("file_path") or params.get("TargetFile")
                   or params.get("AbsolutePath") or "")
        if path and path not in out["files"]:
            out["files"].append(path)
        out["events"].append({"type": "file_change", "tool": tool, "path": path})
    else:
        out["events"].append({"type": "tool", "tool": tool})
