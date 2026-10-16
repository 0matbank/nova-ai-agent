"""Core → broker calls over the named pipe (Phase 22).

The broker is started by Windows (scheduled task "\\Nova AI\\Broker", highest
privileges, at logon). Core never elevates anything itself; if the broker is
installed but not running, core asks Task Scheduler to start that task.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.config.schema import AppConfig
from core.ipc.token import TokenStore
from core.log import get_logger

_log = get_logger("core")

ERRORS_BN = {
    "APPROVAL_REQUIRED": "broker নিজে owner-এর approval খুঁজে পায়নি",
    "ACTION_NOT_ALLOWED": "broker-এর allowlist-এ নেই",
    "BAD_PARAMS": "ভুল parameter",
    "LOCKED_DOWN": "/lockdown চালু — admin কাজ বন্ধ",
    "NOT_ELEVATED": "broker administrator হিসেবে চলছে না",
    "ACTION_FAILED": "কাজটা হয়নি",
    "UNAUTHORIZED": "broker token মেলেনি",
    "PROTOCOL_MISMATCH": "broker-এর version পুরনো — scripts/broker_install.py আবার চালাতে হবে",
}


class BrokerUnavailable(Exception):
    pass


class BrokerError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)

    def bangla(self) -> str:
        base = ERRORS_BN.get(self.code, self.code)
        return f"{base} ({self.detail})" if self.detail else base


class BrokerClient:
    def __init__(self, cfg: AppConfig, tokens: TokenStore) -> None:
        b = cfg.workers.workers.privileged_broker
        self.enabled = b.enabled
        self.pipe = b.pipe_name
        self.task_name = b.scheduled_task
        self.install_dir = Path(b.install_dir)
        self.protocol_version = cfg.workers.protocol_version
        self.tokens = tokens

    @property
    def installed(self) -> bool:
        return (self.install_dir / "policy.json").is_file()

    def _call_sync(self, action: str, params: dict[str, Any], target: str,
                   task_id: int | str, connect_ms: int) -> dict[str, Any]:
        if sys.platform != "win32":
            raise BrokerUnavailable("the broker is Windows-only")
        import pywintypes

        from core.ipc.pipe import pipe_call
        msg = {"protocol_version": self.protocol_version, "request_id": uuid.uuid4().hex,
               "task_id": str(task_id), "token": self.tokens.current(), "action": action,
               "target": target, "params": params}
        try:
            return pipe_call(self.pipe, msg, timeout_ms=connect_ms)
        except pywintypes.error as e:
            raise BrokerUnavailable(f"broker not reachable ({e.strerror})") from None

    async def call(self, action: str, params: dict[str, Any] | None = None, *,
                   target: str = "", task_id: int | str = "system",
                   connect_ms: int = 3000) -> dict[str, Any]:
        reply = await asyncio.to_thread(self._call_sync, action, params or {}, target,
                                        task_id, connect_ms)
        if not reply.get("ok"):
            raise BrokerError(str(reply.get("error", "ERROR")), str(reply.get("detail", "")))
        result: dict[str, Any] = reply.get("result") or {}
        return result

    async def status(self) -> dict[str, Any]:
        return await self.call("status", connect_ms=1500)

    async def lockdown(self, on: bool) -> None:
        await self.call("lockdown", {"on": on}, connect_ms=1500)

    def start_task(self) -> bool:
        """Ask Task Scheduler to start the installed broker task (no UAC needed)."""
        if sys.platform != "win32" or not self.installed:
            return False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        import win32api
        schtasks = str(Path(win32api.GetSystemDirectory()) / "schtasks.exe")
        r = subprocess.run([schtasks, "/run", "/tn", self.task_name],  # noqa: S603 - fixed
                           capture_output=True, creationflags=flags, check=False)
        _log.info(f"broker task start requested (exit {r.returncode})",
                  extra={"action": "broker.start", "status": "ok" if r.returncode == 0
                         else "error"})
        return r.returncode == 0

    async def execute(self, op: str, params: dict[str, Any], target: str,
                      task_id: int | str) -> dict[str, Any]:
        """An admin operation; long ones (winget, scans) may take many minutes."""
        return await self.call(op, params, target=target, task_id=task_id)

    async def ensure_running(self) -> dict[str, Any] | None:
        """Status of the running broker; start the installed task once if needed."""
        try:
            return await self.status()
        except BrokerUnavailable:
            pass
        if not self.start_task():
            return None
        for _ in range(20):
            await asyncio.sleep(0.5)
            try:
                return await self.status()
            except BrokerUnavailable:
                continue
        return None


# Tests swap in a broker that answers in-process (no pipe, no elevation).
_factory: Callable[[AppConfig], Any] | None = None


def set_factory(factory: Callable[[AppConfig], Any] | None) -> None:
    global _factory
    _factory = factory


def client_for(cfg: AppConfig) -> BrokerClient:
    if _factory is not None:
        client: BrokerClient = _factory(cfg)
        return client
    return BrokerClient(cfg, TokenStore(cfg.path("secrets_dir")))
