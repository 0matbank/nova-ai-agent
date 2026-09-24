"""Phase 5: privileged broker interface over a restricted named pipe (Windows)."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from core.ipc.token import TokenStore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")


@pytest.fixture
def broker(tmp_path: Path) -> Iterator[tuple[str, TokenStore]]:
    from core.ipc.pipe import PipeServer
    tokens = TokenStore(tmp_path)
    tokens.ensure()
    name = rf"\\.\pipe\nova-test-{uuid.uuid4().hex[:12]}"
    server = PipeServer(name, 1, tokens, {"ping": lambda p: {"pong": True}})
    server.start()
    yield name, tokens
    server.stop()


def msg(tokens: TokenStore, **over):  # type: ignore[no-untyped-def]
    m = {"protocol_version": 1, "request_id": uuid.uuid4().hex, "task_id": "system",
         "token": tokens.current(), "action": "ping", "params": {}}
    m.update(over)
    return m


def test_ping(broker) -> None:  # type: ignore[no-untyped-def]
    from core.ipc.pipe import pipe_call
    name, tokens = broker
    assert pipe_call(name, msg(tokens)) == {"ok": True, "result": {"pong": True}}


@pytest.mark.parametrize("over, error", [
    ({"token": "wrong-token-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"}, "UNAUTHORIZED"),
    ({"token": ""}, "UNAUTHORIZED"),
    ({"action": "run_shell", "params": {"cmd": "whoami"}}, "ACTION_NOT_ALLOWED"),
    ({"action": "service.restart"}, "ACTION_NOT_ALLOWED"),
    ({"protocol_version": 2}, "PROTOCOL_MISMATCH"),
    ({"request_id": "x"}, "BAD_REQUEST"),
    ({"task_id": "abc"}, "BAD_REQUEST"),
])
def test_rejections(broker, over, error) -> None:  # type: ignore[no-untyped-def]
    from core.ipc.pipe import pipe_call
    name, tokens = broker
    reply = pipe_call(name, msg(tokens, **over))
    assert reply["ok"] is False and reply["error"] == error


def test_malformed_json(broker) -> None:  # type: ignore[no-untyped-def]
    import json

    import win32file
    import win32pipe
    name, _ = broker
    win32pipe.WaitNamedPipe(name, 2000)
    h = win32file.CreateFile(name, win32file.GENERIC_READ | win32file.GENERIC_WRITE, 0, None,
                             win32file.OPEN_EXISTING, 0, None)
    try:
        win32pipe.SetNamedPipeHandleState(h, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        win32file.WriteFile(h, b"{not json")
        _, data = win32file.ReadFile(h, 65536)
        assert json.loads(bytes(data))["error"] == "BAD_REQUEST"
    finally:
        win32file.CloseHandle(h)


def test_dacl_only_user_and_system() -> None:
    import win32api
    import win32security

    from core.ipc.pipe import restricted_security_attributes
    sa = restricted_security_attributes()
    dacl = sa.SECURITY_DESCRIPTOR.GetSecurityDescriptorDacl()
    sids = {win32security.ConvertSidToStringSid(dacl.GetAce(i)[2])
            for i in range(dacl.GetAceCount())}
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                           win32security.TOKEN_QUERY)
    me = win32security.ConvertSidToStringSid(
        win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    assert sids == {me, "S-1-5-18"}                 # current user + LocalSystem only
    assert "S-1-1-0" not in sids                    # no Everyone
