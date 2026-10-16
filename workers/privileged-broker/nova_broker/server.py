"""Request handling + the named pipe.

Message: {"protocol_version", "request_id", "task_id", "token", "action", "target",
"params"} → {"ok": true, "result": {...}} or {"ok": false, "error", "detail"}.

Pipe security: the first instance is created with FILE_FLAG_FIRST_PIPE_INSTANCE (no
one can squat the name first); the DACL lets the owner's normal (non-elevated)
session read and write but NOT create further instances, network logons are denied,
and an explicit Medium integrity label lets the non-elevated core connect.
"""

from __future__ import annotations

import contextlib
import ctypes
import hmac
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from nova_broker import VERSION
from nova_broker.approvals import ApprovalChecker
from nova_broker.ops import (
    HANDLERS,
    Backend,
    BadParams,
    Failed,
    NotAllowed,
    required_action,
    validate,
)
from nova_broker.policy import Policy

BUFSIZE = 65536
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
SAFE_IN_LOCKDOWN = {"ping", "status", "lockdown"}
# user: FILE_GENERIC_READ | FILE_WRITE_DATA | FILE_WRITE_ATTRIBUTES — no
# FILE_CREATE_PIPE_INSTANCE (= FILE_APPEND_DATA), so no instance squatting
USER_PIPE_ACCESS = 0x0012018B
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000

log = logging.getLogger("nova_broker")


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


class Broker:
    def __init__(self, policy: Policy, backend: Backend | None = None,
                 elevated: bool | None = None) -> None:
        self.policy = policy
        self.backend = backend or Backend()
        self.approvals = ApprovalChecker(policy.db_path, policy.state_dir,
                                         policy.approval_max_age_seconds)
        self.elevated = is_elevated() if elevated is None else elevated
        self._lock = threading.Lock()                 # one admin change at a time
        self._lockdown_file = policy.state_dir / "lockdown"
        self._token: tuple[float, str] | None = None

    # ------------------------------------------------------------ helpers

    @property
    def locked_down(self) -> bool:
        return self._lockdown_file.exists()

    def _token_ok(self, presented: str) -> bool:
        try:
            mtime = self.policy.token_file.stat().st_mtime
            if self._token is None or self._token[0] != mtime:
                self._token = (mtime, self.policy.token_file.read_text(encoding="utf-8").strip())
        except OSError:
            return False
        real = self._token[1]
        return len(real) >= 32 and hmac.compare_digest(presented.encode(), real.encode())

    def status(self) -> dict[str, Any]:
        return {"version": VERSION, "protocol_version": self.policy.protocol_version,
                "elevated": self.elevated, "lockdown": self.locked_down, "pid": os.getpid(),
                "policy": self.policy.summary()}

    @staticmethod
    def _err(code: str, detail: str = "") -> dict[str, Any]:
        return {"ok": False, "error": code, "detail": detail}

    # ------------------------------------------------------------ handling

    def handle(self, raw: bytes, client_pid: int | None = None) -> dict[str, Any]:
        try:
            req = json.loads(raw.decode("utf-8"))
            assert isinstance(req, dict)
        except (ValueError, AssertionError):
            return self._err("BAD_REQUEST")
        if not self._token_ok(str(req.get("token", ""))):
            log.warning("rejected: invalid token (client pid %s)", client_pid)
            return self._err("UNAUTHORIZED")
        rid, task = req.get("request_id"), str(req.get("task_id", ""))
        if not (isinstance(rid, str) and _REQUEST_ID.match(rid)) or not (
                task == "system" or task.isdigit()):
            return self._err("BAD_REQUEST")
        if req.get("protocol_version") != self.policy.protocol_version:
            return {**self._err("PROTOCOL_MISMATCH"),
                    "protocol_version": self.policy.protocol_version}
        op = str(req.get("action", ""))
        params = req.get("params") or {}
        if op == "ping":
            return {"ok": True, "result": {"pong": True, "pid": os.getpid()}}
        if op == "status":
            return {"ok": True, "result": self.status()}
        if op == "lockdown":
            on = params.get("on") is True
            self.policy.state_dir.mkdir(parents=True, exist_ok=True)
            if on:
                self._lockdown_file.write_text("1", encoding="utf-8")
            else:
                self._lockdown_file.unlink(missing_ok=True)
            log.warning("lockdown %s (task %s)", "ON" if on else "off", task)
            return {"ok": True, "result": {"lockdown": on}}
        if not isinstance(params, dict):
            return self._err("BAD_PARAMS", "params must be an object")
        try:
            params = validate(op, params)
        except NotAllowed:
            log.warning("rejected: action not allowlisted: %r (client pid %s)", op, client_pid)
            return self._err("ACTION_NOT_ALLOWED")
        except BadParams as e:
            return self._err("BAD_PARAMS", str(e))
        cancel = op == "power" and params["op"] == "cancel"
        if self.locked_down and not cancel:
            return self._err("LOCKED_DOWN", "the owner turned on /lockdown")
        action = required_action(op, params)
        approval_id = None
        if action is not None:
            approval_id, why = self.approvals.claim(task, action, str(req.get("target", "")),
                                                    params)
            if approval_id is None:
                log.warning("refused %s %s for task %s: %s", op, params, task, why)
                return self._err("APPROVAL_REQUIRED", why)
        if self.policy.require_elevation and not self.elevated and action is not None:
            return self._err("NOT_ELEVATED", "the broker is not running as administrator")
        log.info("run %s %s task=%s approval=%s client=%s", op, params, task, approval_id,
                 client_pid)
        try:
            with self._lock:
                result = HANDLERS[op](params, self.policy, self.backend)
        except NotAllowed as e:
            return self._err("ACTION_NOT_ALLOWED", str(e))
        except Failed as e:
            log.error("failed %s: %s", op, e)
            return self._err("ACTION_FAILED", str(e))
        except Exception as e:  # noqa: BLE001 - report, never crash the broker
            log.exception("error in %s", op)
            return self._err("ACTION_FAILED", f"{type(e).__name__}: {e}"[:300])
        result["approval_id"] = approval_id
        log.info("done %s: %s", op, result.get("summary"))
        return {"ok": True, "result": result}


# ------------------------------------------------------------------- pipe

def pipe_sddl(elevated: bool, user_sid: str) -> str:
    """Elevated (production): owned by Administrators; the owner's normal session may
    only read/write. Unelevated (development/tests) the broker IS that same user and
    needs FILE_CREATE_PIPE_INSTANCE itself for every further instance."""
    if elevated:
        return (f"O:BAG:BAD:P(D;;FA;;;NU)(A;;FA;;;SY)(A;;FA;;;BA)"
                f"(A;;0x{USER_PIPE_ACCESS:x};;;{user_sid})S:(ML;;NW;;;ME)")
    return f"D:P(D;;FA;;;NU)(A;;FA;;;SY)(A;;FA;;;{user_sid})S:(ML;;NW;;;ME)"


def pipe_security(elevated: bool) -> Any:
    import win32api
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                           win32security.TOKEN_QUERY)
    me = win32security.ConvertSidToStringSid(
        win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    sddl = pipe_sddl(elevated, me)
    sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, win32security.SDDL_REVISION_1)
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = sd
    return sa


class PipeServer:
    def __init__(self, broker: Broker) -> None:
        self.broker = broker
        self.name = broker.policy.pipe_name
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.error: BaseException | None = None
        self._thread: threading.Thread | None = None

    def _serve_one(self, handle: Any) -> None:
        import pywintypes
        import win32file
        import win32pipe
        try:
            try:
                pid: int | None = win32pipe.GetNamedPipeClientProcessId(handle)
            except (pywintypes.error, AttributeError):
                pid = None
            _, data = win32file.ReadFile(handle, BUFSIZE)
            reply = json.dumps(self.broker.handle(bytes(data), pid)).encode("utf-8")
            win32file.WriteFile(handle, reply[:BUFSIZE])
            win32file.FlushFileBuffers(handle)
        except pywintypes.error:
            pass                                       # client vanished mid-call
        finally:
            with contextlib.suppress(pywintypes.error):
                win32pipe.DisconnectNamedPipe(handle)
            win32file.CloseHandle(handle)

    def serve_forever(self) -> None:
        import win32pipe
        sa = pipe_security(self.broker.elevated)
        mode = (win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE
                | win32pipe.PIPE_WAIT | win32pipe.PIPE_REJECT_REMOTE_CLIENTS)
        first = True
        while not self._stop.is_set():
            open_mode = win32pipe.PIPE_ACCESS_DUPLEX | (FILE_FLAG_FIRST_PIPE_INSTANCE
                                                         if first else 0)
            try:
                handle = win32pipe.CreateNamedPipe(self.name, open_mode, mode,
                                                   win32pipe.PIPE_UNLIMITED_INSTANCES,
                                                   BUFSIZE, BUFSIZE, 0, sa)
            except Exception as e:                     # e.g. the name is already taken
                self.error = e
                self._ready.set()
                return
            first = False
            self._ready.set()
            import pywintypes
            with contextlib.suppress(pywintypes.error):   # client came and went
                win32pipe.ConnectNamedPipe(handle, None)
            if self._stop.is_set():
                import win32file
                win32file.CloseHandle(handle)
                break
            threading.Thread(target=self._serve_one, args=(handle,), daemon=True,
                             name="broker-call").start()

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True,
                                        name="broker-pipe")
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError(f"broker pipe {self.name} did not come up")
        if self.error is not None:
            raise RuntimeError(f"cannot own {self.name}: {self.error}")

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(Exception):            # unblock ConnectNamedPipe
            import win32file
            h = win32file.CreateFile(self.name, USER_PIPE_ACCESS, 0, None,
                                     win32file.OPEN_EXISTING, 0, None)
            win32file.CloseHandle(h)
        if self._thread is not None:
            self._thread.join(timeout=5)


def setup_logging(log_dir: Path) -> None:
    from logging.handlers import RotatingFileHandler
    log_dir.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(log_dir / "broker.log", maxBytes=2_000_000, backupCount=5,
                            encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
