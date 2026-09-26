"""Google Antigravity provider (plan §8.2, Phase 13): Google's official
Antigravity CLI (`agy`), signed in with the owner's Google account, so the
Google AI Pro plan's quota is used — not Gemini API billing.

- Credentials stay in the CLI's own store (the system keyring); Nova never
  reads or keeps them (owner rule 2026-09-25). Health = `agy models`, which
  answers in about a second, costs no quota and says "Please sign in" when
  there is no session — plus a check that agy's own settings still carry
  Nova's rules (providers/google_antigravity/settings.py).
- Non-interactive print mode with NDJSON in and out (`--print=` with
  `--input-format stream-json --output-format stream-json`): the prompt goes
  in on stdin (no command-line length or quoting limits). stdin stays open
  until the `result` event — agy cancels a running tool call when stdin closes.
- Safety, verified live on Windows (2026-09-26): terminal commands are always
  refused in print mode (toolPermission request-review; agy's Windows sandbox
  is a preview that needs admin, so `--sandbox` is not used). File edits only
  when the request allows "code.edit" for its workspace — then `--mode
  accept-edits`, which stays inside the workspace. Web fetching is denied in
  the settings. Never `--dangerously-skip-permissions`. The core authorises
  code.edit first (Coding flow), runs the tests itself and checks that nothing
  was committed.
- Without a workspace the agent runs in an empty scratch folder, so it has
  nothing of the owner's to look at.
- Quota / 429 → RATE_LIMIT + cooldown, sign-in problems → AUTH, so the router
  moves on to the next provider (plan §8.5, §8.7).
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

from providers.google_antigravity import settings as agy_settings
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
                    r"usage limit|out of credits", re.IGNORECASE)
_AUTH = re.compile(r"authenticat|sign ?in|log ?in|unauthori[sz]ed|\b401\b|token.*expired",
                   re.IGNORECASE)
# CLI start-up noise on stderr that is never the reason for a failure
_NOISE = re.compile(r"logging before google\.Init|oauth2/auth\?|^\s*$")
# agy links files as [name](file:///D:/...#L4-L8) — plain names read better on Telegram
_FILE_LINK = re.compile(r"\[([^\]]+)\]\(file:///[^)\s]*\)")
EDIT_TOOLS = frozenset({"write_to_file", "replace_file_content", "multi_replace_file_content",
                        "sed_file", "notebook_edit"})
COMMAND_TOOLS = frozenset({"run_command", "send_command_input"})
# The agent's tools differ from Codex's; this tells it what works here.
TOOL_NOTE = ("IMPORTANT — tools in this run: terminal commands are refused, so never call "
             "run_command (not even to list files or run tests). Use only your file tools: "
             "find_by_name, grep_search, view_file, replace_file_content, write_to_file. The owner "
             "runs the tests afterwards. Do not search or open the web. Stay inside the "
             "current folder.")
# When the agent still tries a command, print mode refuses it and ends the turn with
# no answer; the session is still open, so it is told once more and carries on.
NUDGE = ("That terminal command was refused, and every command will be refused in this run. "
         "Do not call run_command again. Carry on with the task using only your file tools "
         "(find_by_name, grep_search, view_file, replace_file_content, write_to_file), then give "
         "your summary.")
MAX_NUDGES = 2


def default_cli() -> str:
    found = shutil.which("agy")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    return str(Path(local) / "agy" / "bin" / "agy.exe") if local else "agy"


class AntigravityAdapter(ProviderAdapter):
    name = "google_antigravity"
    capabilities = frozenset({"coding", "review", "reasoning", "summarization", "planning"})

    def __init__(self, model: str | None = None, agy_cmd: list[str] | None = None,
                 scratch_dir: Path | None = None, settings_file: Path | None = None) -> None:
        super().__init__()
        self.model = model
        self.cmd = agy_cmd or [default_cli()]
        self.scratch_dir = scratch_dir
        self.settings_file = settings_file
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
        try:
            return await asyncio.wait_for(self._talk(proc, stdin), timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise ProviderError(ErrorCategory.TIMEOUT, f"agy timed out after {timeout:.0f}s"
                                ) from None

    @staticmethod
    async def _talk(proc: asyncio.subprocess.Process, stdin: str | None
                    ) -> tuple[int, str, str]:
        assert proc.stdout is not None and proc.stderr is not None
        err_task = asyncio.ensure_future(proc.stderr.read())
        if stdin is not None and proc.stdin is not None:
            proc.stdin.write(stdin.encode("utf-8"))
            await proc.stdin.drain()
        lines: list[bytes] = []
        nudges = 0
        while line := await proc.stdout.readline():
            lines.append(line)
            result = _result_of(line)
            if result is None:
                continue
            if (stdin is not None and proc.stdin is not None and nudges < MAX_NUDGES
                    and _stopped_on_refused_command(result)):
                nudges += 1
                proc.stdin.write(_user_message(NUDGE).encode("utf-8"))
                await proc.stdin.drain()
                continue
            break                        # the turn is over: now stdin may close
        if proc.stdin is not None:
            proc.stdin.close()
        lines.append(await proc.stdout.read())
        code = await proc.wait()
        err = await err_task
        return (code or 0, b"".join(lines).decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))

    # --------------------------------------------------------------- health

    async def check_health(self) -> Health:
        if not self.installed():
            return self._set(HealthState.UNAVAILABLE, "Antigravity CLI (agy) not installed")
        left = self.cooldown_until - time.time()
        if left > 0:
            return self._set(HealthState.COOLDOWN,
                             f"quota used up — retry in {int(left // 60)} min")
        drift = agy_settings.missing(agy_settings.read(self.settings_file))
        if drift:
            # never run the agent without Nova's rules in its settings
            return self._set(HealthState.UNAVAILABLE,
                             "agy settings lack Nova's safety rules — run "
                             f"scripts/antigravity_setup.py ({', '.join(drift[:3])})")
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
        # `--print=` (empty): the prompt comes on stdin; a bare --print eats the next flag
        args = ["--print=", "--input-format", "stream-json", "--output-format", "stream-json",
                "--print-timeout", f"{int(request.limits.timeout_seconds)}s",
                "--disable-slash-commands"]
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
        message = _user_message(_prompt(request))
        # a little longer than the CLI's own --print-timeout, so its clean error wins
        code, out, err = await self._run(self.args_for(request), self.cwd_for(request),
                                         request.limits.timeout_seconds + 30, stdin=message)
        parsed = parse_stream(out)
        status = str(parsed["status"]).upper()
        failure = parsed["error"]
        if not failure and not parsed["result_seen"]:
            failure = _reason(err, out) or f"agy exited {code} without a result"
        if not failure and status not in ("SUCCESS", ""):
            failure = status
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
            if "timed out" in text.lower() or status in ("INTERRUPTED", "CANCELED"):
                raise ProviderError(ErrorCategory.TIMEOUT, f"agy: {failure[:200]}")
            raise ProviderError(ErrorCategory.SERVER_ERROR, f"agy failed: {failure[:300]}")
        hints = []
        if not parsed["answer"]:
            hints.append("empty answer")
        if parsed["denied"]:
            hints.append("agy refused: " + ", ".join(parsed["denied"]))
        usage = parsed["usage"]
        return ProviderResult(
            ResultStatus.OK, self.name, model=parsed["model"] or self.model or "agy-default",
            answer=parsed["answer"], files_changed=tuple(parsed["files"]),
            commands=tuple(parsed["commands"]), tool_events=tuple(parsed["events"]),
            session_id=parsed["session_id"],
            usage=Usage(input_tokens=int(usage.get("input_tokens", 0) or 0),
                        output_tokens=int(usage.get("output_tokens", 0) or 0),
                        seconds=time.perf_counter() - t0),
            verification_hints=tuple(hints))


def _user_message(text: str) -> str:
    return json.dumps({"event": "user", "message": {"content": text}}, ensure_ascii=False) + "\n"


def _result_of(line: bytes) -> dict[str, Any] | None:
    """The `result` event's body when this stdout line is one (end of a turn)."""
    if not line.lstrip().startswith(b"{") or b'"result"' not in line:
        return None
    try:
        e = json.loads(line)
    except ValueError:
        return None
    body = e.get("result") if isinstance(e, dict) and e.get("event") == "result" else None
    return body if isinstance(body, dict) else None


def _stopped_on_refused_command(body: dict[str, Any]) -> bool:
    """A result that ended because a terminal command was refused, with no answer."""
    denied = {str(d.get("action")) for d in body.get("denied_actions") or []
              if isinstance(d, dict)}
    return (body.get("status") == "SUCCESS" and not str(body.get("response") or "").strip()
            and bool(denied & {"command", "escalate_admin"}))


def _prompt(request: ProviderRequest) -> str:
    parts = [p for p in (TOOL_NOTE, request.system, request.context) if p]
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


def parse_stream(stdout: str) -> dict[str, Any]:
    """`agy --output-format stream-json` → answer / status / error / files /
    commands / refused actions / conversation id / usage. Each line is
    {"event": <kind>, <kind>: {...}}; a bare envelope (--output-format json)
    works too. Unknown events and lines are ignored."""
    out: dict[str, Any] = {"answer": "", "status": "", "error": "", "files": [],
                           "commands": [], "events": [], "denied": [], "session_id": None,
                           "usage": {}, "model": None, "result_seen": False}
    deltas: list[str] = []
    for line in stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        kind = str(e.get("event") or e.get("type") or "")
        nested = e.get(kind)
        body: dict[str, Any] = nested if isinstance(nested, dict) else e
        if kind == "init":
            out["session_id"] = e.get("conversation_id") or body.get("conversation_id")
            out["model"] = body.get("model") or out["model"]
        elif kind == "step_update":
            if body.get("step_type") == "agent_response" and body.get("text_delta"):
                deltas.append(str(body["text_delta"]))
            tool = str(body.get("tool_name") or "")
            if tool and str(body.get("state", "")).upper() in ("DONE", "ERROR"):
                _tool_step(out, tool, body.get("tool_info"))
        elif kind == "result" or ("status" in body and "response" in body):
            out["result_seen"] = True
            out["status"] = str(body.get("status") or "")
            out["error"] = str(body.get("error") or "")
            out["answer"] = str(body.get("response") or "").strip()
            out["session_id"] = body.get("conversation_id") or out["session_id"]
            out["usage"] = body.get("usage") or {}
            for d in body.get("denied_actions") or []:
                action = str(d.get("action", "?")) if isinstance(d, dict) else "?"
                if action not in out["denied"]:
                    out["denied"].append(action)
    if not out["answer"] and deltas and not out["error"]:
        out["answer"] = "".join(deltas).strip()
    out["answer"] = _FILE_LINK.sub(r"\1", out["answer"])
    return out


def _tool_step(out: dict[str, Any], tool: str, info: Any) -> None:
    info = info if isinstance(info, dict) else {}
    params = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
    error = info.get("error") if isinstance(info.get("error"), dict) else None
    event: dict[str, Any] = {"type": "tool", "tool": tool}
    if tool in COMMAND_TOOLS:
        cmd = str(params.get("CommandLine") or params.get("command") or "")[:300]
        event = {"type": "command", "tool": tool, "command": cmd}
        if cmd and not error:
            out["commands"].append(cmd)
    elif tool in EDIT_TOOLS:
        path = str(params.get("TargetFile") or params.get("path") or "")
        event = {"type": "file_change", "tool": tool, "path": path}
        if path and not error and path not in out["files"]:
            out["files"].append(path)
    if error:
        event["error"] = str(error.get("message", ""))[:300]
    out["events"].append(event)
