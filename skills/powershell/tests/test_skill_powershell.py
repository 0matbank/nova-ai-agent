from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.skills.api import PolicyDenied
from tests.mocks.skills import SkillEnv, make_skill_env

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell skill is Windows-only")


@pytest.fixture
def s(tmp_path: Path) -> SkillEnv:
    return make_skill_env(tmp_path)


def classify(cmd: str) -> bool:
    mod = sys.modules.get("nova_skill_powershell_classify")
    if mod is None:
        import importlib
        importlib.import_module("nova_skill_powershell_run")._classifier()
        mod = sys.modules["nova_skill_powershell_classify"]
    return bool(mod.analyze(cmd).read_only)


GREEN = [
    "Get-Date",
    "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5",
    "Get-ChildItem C:/Users | Where-Object { $_.Length -gt 1MB } | Format-Table",
    "Test-Path C:/x 2>$null",
    "$v = Get-Item .; $v.Name",
    "$env:TEMP_X = 1; Get-Date",
]
RED = [
    "Remove-Item C:/x", "Get-ChildItem | Remove-Item", "Get-ChildItem | % Delete",
    "Get-ChildItem | ForEach-Object { $_ }", "[IO.File]::Delete('C:/x')",
    "(Get-Item x).Delete()", "Get-Process > out.txt", "& 'notepad.exe'", "iex 'rm x'",
    "Get-ChildItem | Where-Object { Remove-Item $_ }", "Start-Process notepad",
    "Get-Date; Stop-Computer", "Get-Date |", "Sort-Object { $_.Kill() }", "Get-Credential",
    "$c='Remove-Item'; & $c x", "(Get-Item f).Attributes = 'Hidden'",
    "${C:/nova.txt} = 'x'", "$a = @(1); $a[0] = 2; Get-Date",
]


@pytest.mark.parametrize("cmd", GREEN)
def test_read_only_commands_are_green(s: SkillEnv, cmd: str) -> None:
    assert classify(cmd) is True


@pytest.mark.parametrize("cmd", RED)
def test_everything_else_is_red(s: SkillEnv, cmd: str) -> None:
    assert classify(cmd) is False


def test_green_command_runs(s: SkillEnv) -> None:
    r = s.call("powershell", "run", {"command": "Get-Date -Format yyyy"})
    assert r.ok and r.data["stdout"].strip().isdigit() and r.untrusted
    assert s.tasks.buttons == []


def test_red_command_blocked_until_approved(s: SkillEnv) -> None:
    target = s.workspace / "made-by-ps.txt"
    cmd = f"New-Item -ItemType File -Path '{target}' | Out-Null"
    _, _ = s.call_with_approval("powershell", "run", {"command": cmd}, approve=False)
    assert not target.exists()
    r, _ = s.call_with_approval("powershell", "run", {"command": cmd})
    assert r is not None and r.ok and target.exists()
    [pending_text] = [t for _, t in s.tasks.sent if "New-Item" in t][:1]
    assert "powershell.run (RED)" in pending_text


def test_protected_path_in_command_refused(s: SkillEnv) -> None:
    for cmd in [f"Get-Content '{s.root / 'secrets' / '.env'}'",
                "Get-Content D:/Personal-Agent/secrets/internal_rpc.token"]:
        with pytest.raises(PolicyDenied):
            s.call("powershell", "run", {"command": cmd})


def test_output_is_redacted(s: SkillEnv) -> None:
    from core.log import add_known_secrets
    add_known_secrets(["nova-test-secret-4242"])
    r = s.call("powershell", "run", {"command": "Write-Output 'nova-test-secret-4242'"})
    assert "nova-test-secret-4242" not in r.data["stdout"]


def test_timeout_kills(s: SkillEnv) -> None:
    # Start-Sleep is RED; exercise the timeout through an approved call.
    r, _ = s.call_with_approval("powershell", "run",
                                {"command": "Start-Sleep 30", "timeout_seconds": 2})
    assert r is not None and not r.ok and "timed out" in r.summary
