"""Public HTTPS address for the local webhook without port forwarding (plan §6: no
VPS / public IP) — a Cloudflare quick tunnel (`cloudflared tunnel --url`): an
outbound connection from this PC, no Cloudflare account, no domain.

A quick tunnel gets a new random *.trycloudflare.com address each start, so Nova
registers the new address with Meta every time (WhatsAppAPI.register_webhook). If
the tunnel process dies it is restarted and re-registered. A fixed public URL (own
named tunnel) can be configured instead."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from core.log import get_logger

_log = get_logger("core")
URL_RX = re.compile(rb"https://[a-z0-9-]+\.trycloudflare\.com")
OnUrl = Callable[[str], Awaitable[None]]


class QuickTunnel:
    def __init__(self, exe: Path, local_port: int, on_url: OnUrl,
                 start_timeout: float = 60) -> None:
        self.exe = exe
        self.local_port = local_port
        self.on_url = on_url
        self.start_timeout = start_timeout
        self.url: str | None = None
        self._proc: asyncio.subprocess.Process | None = None

    async def _start(self) -> str:
        self._proc = await asyncio.create_subprocess_exec(
            str(self.exe), "tunnel", "--no-autoupdate", "--url",
            f"http://127.0.0.1:{self.local_port}", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        assert self._proc.stderr is not None

        async def find() -> str:
            assert self._proc is not None and self._proc.stderr is not None
            async for line in self._proc.stderr:
                m = URL_RX.search(line)
                if m:
                    return m.group(0).decode()
            raise RuntimeError("cloudflared exited before giving an address")
        return await asyncio.wait_for(find(), self.start_timeout)

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 5.0
        while not stop.is_set():
            try:
                self.url = await self._start()
                _log.info(f"whatsapp tunnel up: {self.url}", extra={"action": "whatsapp.tunnel",
                                                                    "status": "ok"})
                await self.on_url(self.url)
                backoff = 5.0
                assert self._proc is not None
                drain = asyncio.create_task(self._drain())
                waiter = asyncio.create_task(self._proc.wait())
                stopper = asyncio.create_task(stop.wait())
                await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
                for t in (drain, waiter, stopper):
                    t.cancel()
                if not stop.is_set():
                    _log.warning("whatsapp tunnel stopped — restarting",
                                 extra={"action": "whatsapp.tunnel", "status": "restart"})
            except Exception as e:  # noqa: BLE001 - keep trying; the owner is alerted by logs
                _log.warning(f"whatsapp tunnel failed: {e}; retry in {backoff:.0f}s",
                             extra={"action": "whatsapp.tunnel", "status": "error"})
            finally:
                await self.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), backoff)
            backoff = min(backoff * 2, 300)

    async def _drain(self) -> None:
        """Keep reading cloudflared's log so its pipe never fills up."""
        assert self._proc is not None and self._proc.stderr is not None
        async for _ in self._proc.stderr:
            pass

    async def close(self) -> None:
        p, self._proc = self._proc, None
        self.url = None
        if p is not None and p.returncode is None:
            p.terminate()
            with contextlib.suppress(TimeoutError, ProcessLookupError):
                await asyncio.wait_for(p.wait(), 10)
            if p.returncode is None:
                p.kill()
