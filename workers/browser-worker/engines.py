"""The Browser Worker's two engines behind one interface (plan §15):

  cli — Microsoft Playwright CLI: one Node run per command; routine work (Layer 1)
  mcp — Microsoft Playwright MCP: a persistent process per session; complex,
        multi-step, agent-driven work (Layer 2) and the coordinate tools used
        by the vision fallback (Layer 3)

Both give the core the same session API, so skills and permissions don't care
which engine runs a session. Everything a page returns is untrusted data.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from browser_cli import NAME_RX, PAGE_INFO_JS, BrowserError, PlaywrightCLI, check_url, find_node
from browser_mcp import MCPError, MCPSession

DESCRIBE_JS = (
    "el => ({tag: el.tagName, type: el.type || null, role: el.getAttribute('role'),"
    " name: el.getAttribute('name'), placeholder: el.getAttribute('placeholder'),"
    " text: (el.innerText || el.getAttribute('aria-label') || el.value || el.title"
    " || '').slice(0,200)})")
# What sits at a point — the clickable ancestor if there is one (vision clicks).
DESCRIBE_XY_JS = (
    "() => {{ const p = document.elementFromPoint({x}, {y}); if (!p) return null;"
    " const el = p.closest('a,button,input,select,textarea,label,summary,[role],[onclick],"
    "[tabindex]') || p;"
    " return {{tag: el.tagName, type: el.type || null, role: el.getAttribute('role'),"
    " name: el.getAttribute('name'), href: el.href || null,"
    " text: (el.innerText || el.getAttribute('aria-label') || el.value || el.title || el.id"
    " || '').slice(0,200)}}; }}")
TEXT_JS = "() => (document.body ? document.body.innerText : '').slice(0, {limit})"
SETTLE_JS = "() => document.readyState + ':' + document.getElementsByTagName('*').length"


# ------------------------------------------------------------------- CLI

class CLIEngine:
    name = "cli"

    def __init__(self, cli: PlaywrightCLI) -> None:
        self.cli = cli

    async def open(self, sid: str | None, url: str | None, profile: str | None,
                   task_id: str | None, headed: bool, device: str | None) -> str:
        s, _ = await self.cli.open(url, profile, task_id, headed, device)
        return s.id

    async def settle(self, sid: str) -> bool:
        return await self.cli.settle(sid)

    async def page_info(self, sid: str) -> dict[str, Any]:
        return await self.cli.page_info(sid)

    async def goto(self, sid: str, url: str) -> None:
        await self.cli.goto(sid, url)

    async def snapshot(self, sid: str) -> Any:
        data = await self.cli.command(sid, "snapshot")
        return data.get("snapshot", data)

    async def find(self, sid: str, text: str) -> Any:
        return await self.cli.command(sid, "find", text)

    async def text(self, sid: str) -> str:
        return await self.cli.text(sid)

    async def describe(self, sid: str, ref: str) -> Any:
        data = await self.cli.command(sid, "eval", DESCRIBE_JS, ref)
        return parsed(data.get("result"))

    async def click(self, sid: str, ref: str) -> None:
        await self.cli.command(sid, "click", ref)

    async def fill(self, sid: str, ref: str, text: str, submit: bool) -> None:
        await self.cli.command(sid, "fill", ref, text, *(["--submit"] if submit else []))

    async def press(self, sid: str, key: str) -> None:
        await self.cli.command(sid, "press", key)

    async def select(self, sid: str, ref: str, value: str) -> None:
        await self.cli.command(sid, "select", ref, value)

    async def back(self, sid: str) -> None:
        await self.cli.command(sid, "go-back")

    async def screenshot(self, sid: str) -> bytes:
        data = await self.cli.command(sid, "screenshot")
        # CLI answers with markdown: "- [Screenshot of viewport](.playwright-cli\page-….png)"
        m = re.search(r"\(([^)]+\.png)\)", str(data.get("result", "")))
        path = Path(m.group(1)) if m else Path()
        if not path.is_absolute():
            path = self.cli.workdir / path
        if not path.is_file():
            raise BrowserError("screenshot file not produced")
        png = path.read_bytes()
        path.unlink(missing_ok=True)          # page captures may hold private data
        return png

    async def describe_xy(self, sid: str, x: int, y: int) -> Any:
        raise BrowserError("coordinate actions need the mcp engine")

    async def click_xy(self, sid: str, x: int, y: int) -> None:
        raise BrowserError("coordinate actions need the mcp engine")

    async def close(self, sid: str) -> None:
        await self.cli.close(sid)

    def sessions(self) -> dict[str, dict[str, Any]]:
        return {s.id: {"profile": s.profile, "task_id": s.task_id, "headed": s.headed}
                for s in self.cli.sessions.values()}


# ------------------------------------------------------------------- MCP

@dataclass
class _MCP:
    session: MCPSession
    profile: str | None
    task_id: str | None
    headed: bool
    history: list[str] = field(default_factory=list)


class MCPEngine:
    name = "mcp"

    def __init__(self, workdir: Path, profiles_dir: Path) -> None:
        self.workdir = workdir
        self.profiles_dir = profiles_dir
        self.node = find_node() or "node"
        self._s: dict[str, _MCP] = {}

    def _get(self, sid: str) -> _MCP:
        s = self._s.get(sid)
        if s is None:
            raise BrowserError("unknown browser session")
        return s

    async def _call(self, sid: str, tool: str, args: dict[str, Any] | None = None
                    ) -> tuple[str, list[bytes]]:
        try:
            return await self._get(sid).session.call(tool, args)
        except MCPError as e:
            raise BrowserError(str(e)) from None

    async def _eval(self, sid: str, function: str, ref: str | None = None) -> Any:
        args: dict[str, Any] = {"function": function}
        if ref:
            args.update(target=ref, element=f"element {ref}")
        text, _ = await self._call(sid, "browser_evaluate", args)
        m = re.search(r"### Result\s*\n(.*?)(?:\n### |\Z)", text, re.DOTALL)
        return parsed(m.group(1).strip() if m else text)

    async def open(self, sid: str | None, url: str | None, profile: str | None,
                   task_id: str | None, headed: bool, device: str | None) -> str:
        if profile is not None and not NAME_RX.match(profile):
            raise BrowserError("profile name must be lowercase letters, digits or '-'")
        sid = sid or uuid.uuid4().hex
        session = MCPSession(self.node, self.workdir,
                             self.profiles_dir / profile if profile else None, headed, device)
        try:
            await session.start()
        except MCPError as e:
            await session.close()
            raise BrowserError(str(e)) from None
        self._s[sid] = _MCP(session, profile, task_id, headed)
        if url:
            try:
                await self.goto(sid, url)
            except BrowserError:
                await self.close(sid)
                raise
        return sid

    async def settle(self, sid: str, max_seconds: float = 8.0, every: float = 0.4) -> bool:
        loop = asyncio.get_running_loop()
        deadline, last = loop.time() + max_seconds, ""
        while loop.time() < deadline:
            try:
                state = str(await self._eval(sid, SETTLE_JS))
            except BrowserError:
                state = ""
            if state.startswith("complete:") and state == last:
                return True
            last = state
            await asyncio.sleep(every)
        return False

    async def page_info(self, sid: str) -> dict[str, Any]:
        raw = await self._eval(sid, f"() => {PAGE_INFO_JS}")
        info = parsed(raw) if isinstance(raw, str) else raw      # JSON.stringify'd object
        info = info if isinstance(info, dict) else {}
        return {"url": str(info.get("url", "")), "title": str(info.get("title", "")),
                "challenge": bool(info.get("challenge"))}

    async def goto(self, sid: str, url: str) -> None:
        safe = check_url(url)
        await self._call(sid, "browser_navigate", {"url": safe})
        self._get(sid).history.append(safe)

    async def snapshot(self, sid: str) -> Any:
        text, _ = await self._call(sid, "browser_snapshot", {"boxes": True})
        m = re.search(r"```yaml\n(.*?)```", text, re.DOTALL)
        return m.group(1) if m else text

    async def find(self, sid: str, text: str) -> Any:
        out, _ = await self._call(sid, "browser_find", {"text": text})
        return out

    async def text(self, sid: str) -> str:
        return str(await self._eval(sid, TEXT_JS.format(limit=6000)))

    async def describe(self, sid: str, ref: str) -> Any:
        return await self._eval(sid, DESCRIBE_JS, ref)

    async def click(self, sid: str, ref: str) -> None:
        await self._call(sid, "browser_click", {"element": f"element {ref}", "target": ref})

    async def fill(self, sid: str, ref: str, text: str, submit: bool) -> None:
        await self._call(sid, "browser_type", {"element": f"field {ref}", "target": ref,
                                               "text": text, "submit": submit})

    async def press(self, sid: str, key: str) -> None:
        await self._call(sid, "browser_press_key", {"key": key})

    async def select(self, sid: str, ref: str, value: str) -> None:
        await self._call(sid, "browser_select_option", {"element": f"select {ref}",
                                                        "target": ref, "values": [value]})

    async def back(self, sid: str) -> None:
        await self._call(sid, "browser_navigate_back")

    async def screenshot(self, sid: str) -> bytes:
        text, images = await self._call(sid, "browser_take_screenshot", {"type": "png"})
        for m in re.finditer(r"\(([^)]+\.(?:png|jpe?g))\)", text):   # saved copy → delete
            p = Path(m.group(1))
            (p if p.is_absolute() else self.workdir / p).unlink(missing_ok=True)
        if not images:
            raise BrowserError("screenshot not produced")
        return images[0]

    async def describe_xy(self, sid: str, x: int, y: int) -> Any:
        return await self._eval(sid, DESCRIBE_XY_JS.format(x=int(x), y=int(y)))

    async def click_xy(self, sid: str, x: int, y: int) -> None:
        await self._call(sid, "browser_mouse_click_xy", {"x": int(x), "y": int(y)})

    async def close(self, sid: str) -> None:
        s = self._s.pop(sid, None)
        if s is None:
            raise BrowserError("unknown browser session")
        await s.session.close()
        if not self._s:
            out = self.workdir / "mcp-output"
            if out.is_dir():
                shutil.rmtree(out, ignore_errors=True)

    def sessions(self) -> dict[str, dict[str, Any]]:
        return {sid: {"profile": s.profile, "task_id": s.task_id, "headed": s.headed}
                for sid, s in self._s.items()}


def parsed(value: Any) -> Any:
    """Eval results arrive as JSON text ('{"tag": ...}' or '"some text"')."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
