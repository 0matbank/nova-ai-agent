"""Child processes for skills (V1.2 hardening, PLAN Stonic-4): no inherited secret-named
environment, input closed, output capped, and on a timeout OR a cancelled task the WHOLE
tree ends — a Windows Job Object catches grandchildren whose parent already exited
(psutil walks the tree as the fallback). A command that finishes normally leaves what it
started running (Start-Process notepad stays open)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import psutil

from core.log import get_logger

_log = get_logger("core")

# never handed to a child: anything named like a credential
SECRET_ENV = re.compile(r"TOKEN|SECRET|PASSW|API_?KEY|_KEY$|CREDENTIAL|COOKIE|SESSION",
                        re.IGNORECASE)
READ_CHUNK = 65536
# after the process exits: this long to read the rest of its output
OUTPUT_GRACE = 2.0


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    env.update(extra or {})
    return env


@dataclass
class Finished:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    truncated: bool = False


class _Job:
    """A Windows Job Object without kill-on-close: killed only when we say so."""

    def __init__(self) -> None:
        import win32job
        self._job: Any = win32job            # (its type stubs say this returns None)
        self.handle: Any = self._job.CreateJobObject(None, "")

    def add(self, pid: int) -> None:
        import win32api
        import win32con
        h = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                                 False, pid)
        try:
            self._job.AssignProcessToJobObject(self.handle, h)
        finally:
            win32api.CloseHandle(h)

    def kill(self) -> None:
        self._job.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        import win32api
        api: Any = win32api
        with contextlib.suppress(Exception):
            api.CloseHandle(self.handle)


def kill_tree(pid: int) -> None:
    """Every process under `pid`, then `pid` — one already gone is not an error."""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
    except psutil.Error:
        return
    for p in [*children, parent]:
        with contextlib.suppress(psutil.Error):
            p.kill()


class _Capped:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    async def drain(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while chunk := await stream.read(READ_CHUNK):
            room = self.limit - len(self.data)
            if room > 0:
                self.data += chunk[:room]
            if len(chunk) > room:
                self.truncated = True        # the rest is read and dropped: no stall


async def run_contained(argv: list[str], *, cwd: str, timeout: float,
                        env: dict[str, str] | None = None,
                        max_output: int = 200_000) -> Finished:
    job: _Job | None = None
    if sys.platform == "win32":
        try:
            job = _Job()
        except Exception:  # noqa: BLE001 - no job object: psutil still ends the tree
            job = None
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env if env is not None else clean_env(),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **kwargs)
    if job is not None:
        try:
            job.add(proc.pid)
        except Exception:  # noqa: BLE001 - e.g. a job that forbids nesting
            job.close()
            job = None
    try:
        # asyncio's proc.wait() also waits for the pipes to close — a child the command
        # started keeps them open, so the exit is watched on the process itself
        watched: psutil.Process | None = psutil.Process(proc.pid)
    except psutil.Error:
        watched = None

    async def exited() -> int | None:
        if watched is None:
            return await proc.wait()
        code: int | None
        try:
            code = await asyncio.to_thread(watched.wait)
        except psutil.Error:
            code = proc.returncode
        return int(code) if code is not None else None

    out, err = _Capped(max_output), _Capped(max_output)
    readers = [asyncio.ensure_future(out.drain(proc.stdout)),
               asyncio.ensure_future(err.drain(proc.stderr))]

    def end_tree() -> None:
        if job is not None:
            with contextlib.suppress(Exception):
                job.kill()
        kill_tree(proc.pid)

    async def settle(grace: float) -> None:
        """What the process wrote is read; a pipe a child it started still holds open
        is not waited for (that child keeps running)."""
        _done, pending = await asyncio.wait(readers, timeout=grace)
        for r in pending:
            r.cancel()
        if pending:
            # our ends of those pipes are closed now, not left to leak (the child keeps
            # its own copies and runs on)
            transport = getattr(proc, "_transport", None)
            with contextlib.suppress(Exception):
                if transport is not None:
                    transport.close()

    exit_code: int | None = None
    try:
        exit_code = await asyncio.wait_for(exited(), timeout=timeout)
    except TimeoutError:
        end_tree()
        await settle(OUTPUT_GRACE)
        return Finished(None, bytes(out.data), bytes(err.data), timed_out=True,
                        truncated=out.truncated or err.truncated)
    except asyncio.CancelledError:
        # the task was cancelled: nothing it started keeps running
        end_tree()
        for r in readers:
            r.cancel()
        _log.info(f"process {proc.pid} ended: the task was cancelled",
                  extra={"action": "proc.cancel", "status": "ok"})
        raise
    finally:
        if job is not None:
            job.close()
    await settle(OUTPUT_GRACE)
    return Finished(exit_code, bytes(out.data), bytes(err.data),
                    truncated=out.truncated or err.truncated)
