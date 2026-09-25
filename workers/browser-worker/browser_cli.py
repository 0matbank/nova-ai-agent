"""Browser Worker engine: wraps Microsoft's Playwright CLI (@playwright/cli,
plan §15 Layer 1) with its structured --json output.

- Every browser session has its own ID, separate from task IDs (plan §17A).
- Sessions use the agent's OWN persistent profiles under sessions/browser/
  — never the owner's everyday Chrome profile (plan §16).
- Headless by default, so it works while the PC is locked (plan §5).
- URL policy: only http(s)/about:blank/data:; never file://, browser-internal
  pages, or this PC's loopback services (the worker APIs live there).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.skills.urls import UrlBlocked
from core.skills.urls import check_url as _check_url

HERE = Path(__file__).resolve().parent
CLI_JS = HERE / "node_modules" / "@playwright" / "cli" / "playwright-cli.js"
NAME_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
PAGE_INFO_JS = (
    "JSON.stringify({url: location.href, title: document.title, challenge: "
    "/captcha|are you a robot|not a robot|verify (that )?you are (a )?human|made by a human|"
    "unusual traffic|bots use/i.test((document.body ? document.body.innerText : '')"
    ".slice(0, 5000)) || [...document.querySelectorAll('iframe')].some(f => "
    "/recaptcha|hcaptcha|turnstile|challenges[.]cloudflare/.test(f.src))})")


class BrowserError(Exception):
    pass


def find_node() -> str | None:
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    return node if Path(node).exists() else None


def check_url(url: str) -> str:
    try:
        return _check_url(url)
    except UrlBlocked as e:
        raise BrowserError(str(e)) from None


@dataclass
class Session:
    id: str
    cli_name: str
    profile: str | None
    task_id: str | None
    headed: bool
    history: list[str] = field(default_factory=list)


class PlaywrightCLI:
    def __init__(self, workdir: Path, profiles_dir: Path, timeout: float = 90.0) -> None:
        self.workdir = workdir
        self.profiles_dir = profiles_dir
        self.timeout = timeout
        self.sessions: dict[str, Session] = {}
        self.node = find_node() or "node"

    async def _run(self, cli_name: str, *args: str, timeout: float | None = None
                   ) -> dict[str, Any]:
        if not CLI_JS.exists():
            raise BrowserError("Playwright CLI not installed (npm ci in workers/browser-worker)")
        self.workdir.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            self.node, str(CLI_JS), f"-s={cli_name}", *args, "--json", cwd=str(self.workdir),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout or self.timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise BrowserError(f"browser command {args[0]!r} timed out") from None
        text = out.decode("utf-8", errors="replace").strip()
        try:
            data: dict[str, Any] = json.loads(text) if text else {}
        except ValueError:
            data = {"raw": text}
        if proc.returncode != 0 or "error" in data:
            msg = data.get("error") or err.decode("utf-8", errors="replace").strip() or text
            raise BrowserError(f"{args[0]} failed: {str(msg)[:400]}")
        return data

    def get(self, sid: str) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            raise BrowserError("unknown browser session")
        return s

    async def open(self, url: str | None, profile: str | None = "default",
                   task_id: str | None = None, headed: bool = False, device: str | None = None
                   ) -> tuple[Session, dict[str, Any]]:
        """profile=None → a throwaway in-memory profile (nothing kept after close)."""
        if profile is not None:
            if not NAME_RX.match(profile):
                raise BrowserError("profile name must be lowercase letters, digits or '-'")
            if any(s.profile == profile for s in self.sessions.values()):
                raise BrowserError(f"profile {profile!r} is already open in another session")
        sid = uuid.uuid4().hex
        s = Session(sid, f"nova-{sid[:12]}", profile, task_id, headed)
        args = ["open", *([check_url(url)] if url else []),
                *([f"--profile={self.profiles_dir / profile}"] if profile else [])]
        if headed:
            args.append("--headed")
        if device == "mobile":
            args.append("--mobile")
        elif device:
            args.append(f"--device={device}")
        data = await self._run(s.cli_name, *args)
        self.sessions[sid] = s
        return s, data

    async def command(self, sid: str, *args: str) -> dict[str, Any]:
        return await self._run(self.get(sid).cli_name, *args)

    async def goto(self, sid: str, url: str) -> dict[str, Any]:
        safe = check_url(url)
        data = await self.command(sid, "goto", safe)
        self.get(sid).history.append(safe)
        return data

    async def settle(self, sid: str, max_seconds: float = 8.0, every: float = 0.5) -> bool:
        """Wait until the page has loaded and stopped changing (dynamic results,
        redirects). True when settled, False when it was still busy at the cap."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_seconds
        last = ""
        while loop.time() < deadline:
            try:
                data = await self.command(
                    sid, "eval",
                    "document.readyState + ':' + document.getElementsByTagName('*').length")
                state = _unquote(data.get("result"))
            except BrowserError:
                state = ""                    # mid-navigation: context was replaced
            if state.startswith("complete:") and state == last:
                return True
            last = state
            await asyncio.sleep(every)
        return False

    async def page_info(self, sid: str) -> dict[str, Any]:
        """Where the page is, plus whether it shows a bot check — those need the
        owner (BLOCKING, plan §17A); the agent never tries to solve one."""
        data = await self.command(sid, "eval", PAGE_INFO_JS)
        try:
            info = json.loads(_unquote(data.get("result")))
        except ValueError:
            info = {}
        return {"url": str(info.get("url", "")), "title": str(info.get("title", "")),
                "challenge": bool(info.get("challenge"))}

    async def text(self, sid: str, limit: int = 6000) -> str:
        data = await self.command(
            sid, "eval", f"(document.body ? document.body.innerText : '').slice(0, {int(limit)})")
        return _unquote(data.get("result"))

    async def close(self, sid: str) -> None:
        s = self.get(sid)
        try:
            await self._run(s.cli_name, "close", timeout=30)
        finally:
            self.sessions.pop(sid, None)
            if not self.sessions:
                self._clear_artifacts()

    def _clear_artifacts(self) -> None:
        """CLI snapshot/screenshot files hold page content — don't keep them."""
        art = self.workdir / ".playwright-cli"
        if art.is_dir():
            for f in art.iterdir():
                if f.is_file() and f.suffix in (".yml", ".png", ".jpeg", ".md", ".log"):
                    f.unlink(missing_ok=True)

    async def close_all(self) -> None:
        for sid in list(self.sessions):
            try:
                await self.close(sid)
            except BrowserError:
                self.sessions.pop(sid, None)


def _unquote(value: Any) -> str:
    text = str(value or "")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return str(json.loads(text))
        except ValueError:
            return text[1:-1]
    return text
