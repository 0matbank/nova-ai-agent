"""Playwright MCP engine (plan §15 Layer 2): Microsoft @playwright/mcp as a
persistent child process, spoken to over stdio (MCP = JSON-RPC 2.0, one JSON
message per line). A minimal client on purpose — only initialize, tools/list
and tools/call are needed, so no large SDK dependency.

One MCP server process = one browser session. It stays up between steps, so
multi-step flows don't pay a Node start-up per action (the CLI engine does).
Headless, isolated (in-memory) profile unless an agent profile is given; the
`vision` capability adds coordinate tools for the Layer 3 fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MCP_JS = HERE / "node_modules" / "@playwright" / "mcp" / "cli.js"
PROTOCOL = "2025-06-18"
# Extra layer only (the MCP docs say this is not a security boundary): the
# core's URL policy is the real check, before and after every step.
BLOCKED = "http://127.0.0.1;https://127.0.0.1;http://localhost;https://localhost;http://[::1]"


class MCPError(Exception):
    pass


class MCPSession:
    def __init__(self, node: str, workdir: Path, profile_dir: Path | None = None,
                 headed: bool = False, device: str | None = None,
                 timeout: float = 90.0) -> None:
        self.node = node
        self.workdir = workdir
        self.profile_dir = profile_dir
        self.headed = headed
        self.device = device
        self.timeout = timeout
        self.proc: asyncio.subprocess.Process | None = None
        self._next = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None
        self.tools: dict[str, dict[str, Any]] = {}

    def args(self) -> list[str]:
        out = [str(MCP_JS), "--caps=vision", "--snapshot-boxes", "--viewport-size=1280x720",
               f"--output-dir={self.workdir / 'mcp-output'}", f"--blocked-origins={BLOCKED}",
               "--timeout-settle=1500", "--image-responses=allow"]
        if not self.headed:
            out.append("--headless")
        out += [f"--user-data-dir={self.profile_dir}"] if self.profile_dir else ["--isolated"]
        if self.device == "mobile":
            out.append("--mobile")
        elif self.device:
            out.append(f"--device={self.device}")
        return out

    async def start(self) -> None:
        if not MCP_JS.exists():
            raise MCPError("Playwright MCP not installed (npm ci in workers/browser-worker)")
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.proc = await asyncio.create_subprocess_exec(
            self.node, *self.args(), cwd=str(self.workdir), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=64 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read_loop())
        await self._request("initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "nova-browser-worker", "version": "1"}})
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = await self._request("tools/list", {})
        self.tools = {t["name"]: t for t in listed.get("tools", [])}

    async def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self.proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                fut = self._pending.pop(msg.get("id", -1), None) if "id" in msg else None
                if fut is not None and not fut.done():
                    if "error" in msg:
                        fut.set_exception(MCPError(str(msg["error"].get("message", msg))))
                    else:
                        fut.set_result(msg.get("result") or {})
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MCPError("browser MCP process ended"))
            self._pending.clear()

    async def _request(self, method: str, params: dict[str, Any],
                       timeout: float | None = None) -> dict[str, Any]:
        if self.proc is None or self.proc.returncode is not None:
            raise MCPError("browser MCP process is not running")
        self._next += 1
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[self._next] = fut
        await self._send({"jsonrpc": "2.0", "id": self._next, "method": method,
                          "params": params})
        try:
            return await asyncio.wait_for(fut, timeout or self.timeout)
        except TimeoutError:
            self._pending.pop(self._next, None)
            raise MCPError(f"{method} timed out") from None

    async def call(self, tool: str, arguments: dict[str, Any] | None = None
                   ) -> tuple[str, list[bytes]]:
        """Run one MCP tool → (text, images). Tool-level errors raise MCPError."""
        import base64
        if self.tools and tool not in self.tools:
            raise MCPError(f"unknown browser tool {tool!r}")
        result = await self._request("tools/call", {"name": tool,
                                                    "arguments": arguments or {}})
        texts, images = [], []
        for item in result.get("content", []):
            if item.get("type") == "text":
                texts.append(str(item.get("text", "")))
            elif item.get("type") == "image" and item.get("data"):
                images.append(base64.b64decode(item["data"]))
        text = "\n".join(texts)
        if result.get("isError"):
            raise MCPError(f"{tool} failed: {text[:400]}")
        return text, images

    async def close(self) -> None:
        if self.proc is None:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.call("browser_close"), 15)
        if self.proc.stdin is not None:
            with contextlib.suppress(Exception):
                self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), 10)
        except TimeoutError:
            self.proc.kill()
            await self.proc.wait()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        self.proc = None
