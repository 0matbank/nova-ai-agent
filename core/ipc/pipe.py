"""Named-pipe RPC for the Privileged/Admin Broker (plan §4, §17A).

- Windows Named Pipe, never a localhost HTTP endpoint.
- DACL grants access only to the current user and SYSTEM (no Everyone, no
  network logons); remote clients are rejected (PIPE_REJECT_REMOTE_CLIENTS).
- Each message: {"protocol_version", "request_id", "task_id", "token",
  "action", "params"} → {"ok", ...}. Only allowlisted actions are executed;
  anything else is refused with ACTION_NOT_ALLOWED. It is never a shell.
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
from collections.abc import Callable
from typing import Any

from core.ipc.protocol import RpcError, valid_request_id, valid_task_id
from core.ipc.token import TokenStore
from core.log import get_logger

_audit = get_logger("audit")
BUFSIZE = 65536
Action = Callable[[dict[str, Any]], dict[str, Any]]


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("named-pipe broker is Windows-only")


def restricted_security_attributes() -> Any:
    """SECURITY_ATTRIBUTES whose DACL allows only the current user + SYSTEM."""
    _require_windows()
    import ntsecuritycon
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                           win32security.TOKEN_QUERY)
    user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    system_sid = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
    dacl = win32security.ACL()
    for sid in (user_sid, system_sid):
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, sid)
    sd = win32security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = sd
    return sa


class PipeServer:
    def __init__(self, pipe_name: str, protocol_version: int, tokens: TokenStore,
                 actions: dict[str, Action]) -> None:
        _require_windows()
        self.pipe_name = pipe_name
        self.protocol_version = protocol_version
        self.tokens = tokens
        self.actions = dict(actions)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ handling

    def handle(self, raw: bytes) -> dict[str, Any]:
        try:
            req = json.loads(raw.decode("utf-8"))
            assert isinstance(req, dict)
        except (ValueError, AssertionError):
            return {"ok": False, "error": RpcError.BAD_REQUEST}
        token = str(req.get("token", ""))
        if not token or not self.tokens.matches(token):
            _audit.warning("broker: invalid token", extra={"action": "broker.reject",
                                                          "status": "unauthorized"})
            return {"ok": False, "error": RpcError.UNAUTHORIZED}
        if not valid_request_id(req.get("request_id")) or not valid_task_id(
                str(req.get("task_id", ""))):
            return {"ok": False, "error": RpcError.BAD_REQUEST}
        if req.get("protocol_version") != self.protocol_version:
            return {"ok": False, "error": RpcError.PROTOCOL_MISMATCH,
                    "protocol_version": self.protocol_version}
        action = str(req.get("action", ""))
        fn = self.actions.get(action)
        if fn is None:
            _audit.warning(f"broker: action not allowlisted: {action}",
                           extra={"action": "broker.reject", "status": "not_allowed"})
            return {"ok": False, "error": RpcError.ACTION_NOT_ALLOWED}
        _audit.info(f"broker action {action}", extra={"action": f"broker.{action}",
                                                      "status": "run"})
        try:
            return {"ok": True, "result": fn(dict(req.get("params") or {}))}
        except Exception as e:
            return {"ok": False, "error": "ACTION_FAILED", "detail": type(e).__name__}

    # -------------------------------------------------------------- server

    def serve_forever(self) -> None:
        import pywintypes
        import win32file
        import win32pipe

        sa = restricted_security_attributes()
        mode = (win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE
                | win32pipe.PIPE_WAIT | win32pipe.PIPE_REJECT_REMOTE_CLIENTS)
        while not self._stop.is_set():
            handle = win32pipe.CreateNamedPipe(
                self.pipe_name, win32pipe.PIPE_ACCESS_DUPLEX, mode,
                win32pipe.PIPE_UNLIMITED_INSTANCES, BUFSIZE, BUFSIZE, 0, sa)
            self._ready.set()
            try:
                win32pipe.ConnectNamedPipe(handle, None)
                if self._stop.is_set():
                    break
                _, data = win32file.ReadFile(handle, BUFSIZE)
                reply = json.dumps(self.handle(bytes(data))).encode("utf-8")
                win32file.WriteFile(handle, reply)
                win32file.FlushFileBuffers(handle)
            except pywintypes.error:
                pass                                    # client vanished mid-call
            finally:
                with contextlib.suppress(pywintypes.error):
                    win32pipe.DisconnectNamedPipe(handle)
                win32file.CloseHandle(handle)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True,
                                        name="broker-pipe")
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError(f"broker pipe {self.pipe_name} did not come up")

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(Exception):            # unblock ConnectNamedPipe
            pipe_call(self.pipe_name, {}, timeout_ms=500)
        if self._thread is not None:
            self._thread.join(timeout=5)


def pipe_call(pipe_name: str, message: dict[str, Any], timeout_ms: int = 5000) -> dict[str, Any]:
    """One request/response round-trip to the broker pipe."""
    _require_windows()
    import time

    import pywintypes
    import win32file
    import win32pipe
    import winerror

    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        # Between two clients the server briefly has no listening instance
        # (FILE_NOT_FOUND) or all instances are busy (PIPE_BUSY): retry.
        try:
            win32pipe.WaitNamedPipe(pipe_name, 200)
            handle = win32file.CreateFile(
                pipe_name, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None)
            break
        except pywintypes.error as e:
            retryable = e.winerror in (winerror.ERROR_FILE_NOT_FOUND, winerror.ERROR_PIPE_BUSY,
                                       winerror.ERROR_SEM_TIMEOUT)
            if not retryable or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
    try:
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        win32file.WriteFile(handle, json.dumps(message).encode("utf-8"))
        _, data = win32file.ReadFile(handle, BUFSIZE)
        result: dict[str, Any] = json.loads(bytes(data).decode("utf-8"))
        return result
    finally:
        win32file.CloseHandle(handle)
