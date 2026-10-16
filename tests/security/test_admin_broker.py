"""Phase 22: the Privileged/Admin Broker on its own — strict validation (no shell, no
injection), the same approval fingerprint as core, the same op → permission mapping
as the admin skill, and the named pipe's security (Windows)."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from nova_broker.approvals import fingerprint as broker_fp
from nova_broker.ops import BadParams, NotAllowed, required_action, validate
from pydantic import ValidationError

from core.config import load_config
from core.permissions.approvals import fingerprint as core_fp
from tests.mocks.broker import make_broker

APP = Path(__file__).resolve().parents[2]

FW = {"op": "add", "name": "Block 8080", "direction": "in", "rule_action": "block",
      "protocol": "TCP", "port": "8080"}


@pytest.mark.parametrize("op, params", [
    ("service", {"name": "Spooler & calc", "op": "restart"}),
    ("service", {"name": "Spooler", "op": "delete"}),
    ("service", {"name": "Spooler", "op": "restart", "extra": 1}),       # unknown key
    ("service", {"name": "Spooler"}),                                      # missing key
    ("firewall", {**FW, "name": "x\" dir=out action=allow"}),
    ("firewall", {**FW, "port": "1-70000"}),
    ("firewall", {**FW, "port": "8080; netsh"}),
    ("firewall", {**FW, "protocol": "ANY"}),
    ("package", {"op": "install", "id": "7zip.7zip --override /S"}),
    ("package", {"op": "install", "id": "-h"}),
    ("power", {"op": "restart", "delay_seconds": 0}),                      # no instant reboot
    ("power", {"op": "restart", "delay_seconds": True}),                   # bool is not int
    ("power", {"op": "restart", "delay_seconds": "60"}),
])
def test_bad_parameters_are_refused(op: str, params: dict[str, Any]) -> None:
    with pytest.raises(BadParams):
        validate(op, params)


@pytest.mark.parametrize("op", ["run_shell", "powershell", "exec", "cmd", "service.delete"])
def test_unknown_operations_are_refused(op: str) -> None:
    with pytest.raises(NotAllowed):
        validate(op, {})


def test_fingerprint_is_identical_to_core() -> None:
    for action, target, details in [
            ("admin.service_control", "service Spooler", {"name": "Spooler", "op": "restart"}),
            ("admin.firewall", "firewall rule 'Nova AI - ক'", {**FW, "name": "ক"}),
            ("system.restart", "pc restart", None)]:
        assert broker_fp(action, target, details) == core_fp(action, target, details)


def test_the_skill_and_the_broker_agree_on_what_needs_approval(tmp_path: Path) -> None:
    from core.skills.api import ToolEnv
    from core.skills.paths import PathPolicy
    from core.skills.registry import SkillRegistry
    cfg = load_config()
    reg = SkillRegistry.load(cfg.skills, platform="win32")
    env = ToolEnv(cfg, PathPolicy(cfg))
    green = {"admin.status", "admin.power_cancel"}
    cases = {
        "service": [{"name": "Spooler", "op": o} for o in ("query", "start", "stop", "restart")],
        "firewall": [{**FW, "op": o} for o in ("list", "add", "remove")],
        "package": [{"op": o, "id": "7zip.7zip"} for o in ("install", "uninstall")],
        "power": [{"op": o, "delay_seconds": 60} for o in ("restart", "shutdown", "cancel")],
        "task": [{"name": "flush_dns"}],
    }
    import asyncio
    for tool, plist in cases.items():
        _, t = reg.get("admin", tool)
        for params in plist:
            p = t.params.model_validate(params)
            action = asyncio.run(t.action_for(p, env))
            want = required_action(tool, validate(tool, p.model_dump(mode="json")))
            assert (None if action in green else action) == want, (tool, params)
            level = cfg.permissions.actions[action]
            assert (level.value == "GREEN") == (action in green), action


def test_task_programs_must_live_in_admin_only_folders(tmp_path: Path) -> None:
    import shutil

    from core.config.loader import ConfigError
    cfg_dir = tmp_path / "config"
    shutil.copytree(APP / "config", cfg_dir)
    wy = cfg_dir / "workers.yaml"
    wy.write_text(wy.read_text(encoding="utf-8").replace(
        r"'%SystemRoot%\System32\ipconfig.exe'", r"'C:\Users\me\evil.exe'"), encoding="utf-8")
    with pytest.raises((ConfigError, ValidationError)):
        load_config(cfg_dir, tmp_path / "rt")


def test_status_and_token(tmp_path: Path) -> None:
    import json
    broker, _, token = make_broker(tmp_path, tmp_path / "none.db")

    def ask(**over: Any) -> dict[str, Any]:
        msg = {"protocol_version": 1, "request_id": uuid.uuid4().hex, "task_id": "system",
               "token": token, "action": "status", "params": {}, **over}
        return broker.handle(json.dumps(msg).encode())
    st = ask()
    assert st["ok"] and st["result"]["policy"]["services"] == ["Spooler", "W32Time"]
    assert ask(token="x" * 43)["error"] == "UNAUTHORIZED"
    assert ask(protocol_version=2)["error"] == "PROTOCOL_MISMATCH"
    assert ask(action="task", params={"name": "flush_dns"}, task_id="1")["error"] == \
        "APPROVAL_REQUIRED"                                    # no database → no approval
    assert ask(action="task", params={"name": "flush_dns"})["error"] == "APPROVAL_REQUIRED"


# ------------------------------------------------------------------ the pipe

win_only = pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")


@win_only
def test_pipe_round_trip_and_security(tmp_path: Path) -> None:
    from nova_broker.server import PipeServer

    from core.ipc.pipe import pipe_call
    broker, _, token = make_broker(tmp_path, tmp_path / "none.db", elevated=False)
    server = PipeServer(broker)
    server.start()
    try:
        base = {"protocol_version": 1, "task_id": "system", "token": token, "params": {}}
        pong = pipe_call(server.name, {**base, "request_id": uuid.uuid4().hex, "action": "ping"})
        assert pong["ok"] and pong["result"]["pong"]
        for _ in range(3):                                       # instance after instance
            shell = pipe_call(server.name, {**base, "request_id": uuid.uuid4().hex,
                                            "action": "run_shell", "params": {"cmd": "whoami"}})
            assert shell["error"] == "ACTION_NOT_ALLOWED"
        # a second server cannot take the name while the broker owns it
        twin_broker, _, _ = make_broker(tmp_path / "twin", tmp_path / "none.db",
                                        elevated=False, pipe_name=server.name)
        with pytest.raises(RuntimeError):
            PipeServer(twin_broker).start()
    finally:
        server.stop()


@win_only
def test_production_pipe_dacl_against_a_normal_user_token() -> None:
    """The ELEVATED broker's pipe, checked by Windows itself (AccessCheck) with this
    test's own non-elevated token: read/write yes; creating a pipe instance (= name
    squatting / intercepting core's calls) no; nothing for Everyone or the network."""
    import ntsecuritycon as nsc
    import win32api
    import win32security
    from nova_broker.server import USER_PIPE_ACCESS, pipe_sddl
    tok = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                         win32security.TOKEN_QUERY | win32security.TOKEN_DUPLICATE)
    elevation = win32security.GetTokenInformation(tok, win32security.TokenElevation)
    if elevation:
        pytest.skip("run as a normal (non-elevated) user")
    me = win32security.ConvertSidToStringSid(
        win32security.GetTokenInformation(tok, win32security.TokenUser)[0])
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    psd = ctypes.c_void_p()
    assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        ctypes.c_wchar_p(pipe_sddl(True, me)), 1, ctypes.byref(psd), None)
    imp = win32security.DuplicateToken(tok, win32security.SecurityImpersonation)
    mapping = (wintypes.DWORD * 4)(nsc.FILE_GENERIC_READ, nsc.FILE_GENERIC_WRITE,
                                   nsc.FILE_GENERIC_EXECUTE, nsc.FILE_ALL_ACCESS)

    def allowed(mask: int) -> bool:
        privs = ctypes.create_string_buffer(256)
        plen, granted, ok = wintypes.DWORD(256), wintypes.DWORD(), wintypes.BOOL()
        assert adv.AccessCheck(psd, wintypes.HANDLE(int(imp)), wintypes.DWORD(mask),
                               ctypes.byref(mapping), privs, ctypes.byref(plen),
                               ctypes.byref(granted), ctypes.byref(ok)), ctypes.get_last_error()
        return bool(ok.value) and granted.value == mask
    assert allowed(USER_PIPE_ACCESS)                            # what core's client opens
    assert not allowed(nsc.FILE_APPEND_DATA)                    # FILE_CREATE_PIPE_INSTANCE
    assert not allowed(nsc.WRITE_DAC) and not allowed(nsc.WRITE_OWNER)
    sddl = pipe_sddl(True, me)
    assert "(D;;FA;;;NU)" in sddl and ";;;WD)" not in sddl      # network denied, no Everyone
