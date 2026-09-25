"""desktop-ui: real UIA against a PRIVATE test window only (never user apps),
plus policy tests with a fake worker."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.ipc.client import WorkerDesktopLocked
from core.queue.engine import ApprovalPending
from core.skills.desktop import DesktopUnavailable
from core.skills.ui_policy import dangerous_label
from tests.mocks.desktop import FakeDesktop, real_desktop_client
from tests.mocks.skills import SkillEnv, make_skill_env

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")


@pytest.mark.parametrize("label,word", [
    ("Delete Everything", "delete"), ("Send", "send"), ("Pay now", "pay"),
    ("Don't Save", "don't save"), ("মুছুন", "মুছুন"), ("পাঠান", "পাঠান"),
    ("Nova Safe Button", None), ("Save", None), ("OK", None), ("Sender name", None),
])
def test_danger_words(label: str, word: str | None) -> None:
    assert dangerous_label(label) == word


@pytest.mark.parametrize("process,klass,title,label,shell", [
    ("cmd.exe", "ConsoleWindowClass", "C:\\WINDOWS\\system32\\cmd.exe", "", True),
    ("WindowsTerminal.exe", "CASCADIA_HOSTING_WINDOW_CLASS", "PowerShell", "", True),
    ("powershell.exe", "ConsoleWindowClass", "Windows PowerShell", "", True),
    ("powershell.exe", "WindowsForms10.Window.8.app.0.1", "My Form", "", False),
    ("explorer.exe", "#32770", "Run", "", True),
    ("Code.exe", "Chrome_WidgetWin_1", "x - VS Code", "Terminal 1, pwsh", True),
    ("Code.exe", "Chrome_WidgetWin_1", "x - VS Code", "Editor", False),
    ("notepad.exe", "Notepad", "Untitled - Notepad", "Text editor", False),
])
def test_shell_window_detection(process: str, klass: str, title: str, label: str,
                                shell: bool) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "workers" / "desktop-worker"))
    import desktop_ui
    assert desktop_ui.is_shell_window(process, klass, title, label) is shell


# ------------------------------------------------------------ fake worker

def fake_env(tmp_path: Path, is_shell: bool = False, locked: bool = False) -> SkillEnv:
    resolve = {"window": {"title": "w", "process": "cmd.exe" if is_shell else "app.exe",
                          "is_shell": is_shell},
               "control": {"name": "Go", "automation_id": "", "type": "ButtonControl"}}
    routes = {"/v1/ui/resolve": resolve, "/v1/ui/click": {"control": {"name": "Go"},
              "method": "InvokePattern.Invoke", "window_still_open": True,
              "foreground_title": "w"},
              "/v1/ui/type": {"verified": True, "method": "x", "after": "t"},
              "/v1/ui/focus": {"window": {"title": "w"}, "verified": True},
              "/v1/ui/keys": {"window_still_open": True, "foreground_title": "w"}}
    if locked:
        routes = {k: WorkerDesktopLocked("locked") for k in routes}
    return make_skill_env(tmp_path, {"desktop": FakeDesktop(routes)})


@pytest.mark.parametrize("tool,params", [
    ("type", {"window": "w", "text": "rm -rf /"}),
    ("keys", {"window": "w", "keys": "enter"}),
    ("click", {"window": "w", "name": "Go"}),
])
def test_terminal_target_is_red_shell_input(tmp_path: Path, tool: str, params: dict) -> None:
    s = fake_env(tmp_path, is_shell=True)
    with pytest.raises(ApprovalPending):
        s.call("desktop-ui", tool, params)
    [appr] = s.tasks.approvals.pending()
    assert appr.action == "ui.shell_input"


def test_locked_desktop_raises_unavailable(tmp_path: Path) -> None:
    s = fake_env(tmp_path, locked=True)
    with pytest.raises(DesktopUnavailable) as ei:
        s.call("desktop-ui", "focus", {"window": "w"})
    assert ei.value.reason == "locked"


# ------------------------------------------------- real UIA, private window

@pytest.fixture
def ui(tmp_path: Path):  # type: ignore[no-untyped-def]
    from tests.mocks.uia_window import uia_test_window
    with uia_test_window() as title:
        yield make_skill_env(tmp_path, {"desktop": real_desktop_client(tmp_path)}), title


def focus_retry(fn, attempts: int = 3):  # type: ignore[no-untyped-def]
    """Windows may refuse the foreground while the owner is typing in another app;
    the worker then (correctly) refuses to send keys. Retry a few times."""
    import time
    r = None
    for _ in range(attempts):
        r = fn()
        if r.ok and r.evidence.get("foreground", True) is not False:
            return r
        time.sleep(1.0)
    return r


def value(s: SkillEnv, title: str, **target: str) -> str:
    return str(s.call("desktop-ui", "read_value", {"window": title, **target}).data["value"])


def test_see_windows_and_controls(ui) -> None:  # type: ignore[no-untyped-def]
    s, title = ui
    wins = s.call("desktop-ui", "windows", {}).data["windows"]
    assert any(w["title"] == title for w in wins)
    ctl = s.call("desktop-ui", "inspect", {"window": title}).data["controls"]
    names = {c["name"] for c in ctl}
    assert {"Nova Input", "Nova Safe Button", "Delete Everything"} <= names


def test_type_appends_and_keeps_existing(ui) -> None:  # type: ignore[no-untyped-def]
    s, title = ui
    r = focus_retry(lambda: s.call("desktop-ui", "type", {"window": title, "name": "Nova Input",
                                                          "text": " + nova"}))
    assert r.ok and r.evidence["previous_content_kept"]
    assert value(s, title, name="Nova Input") == "existing text + nova"
    assert s.tasks.buttons == []                                 # BLUE: no approval


def test_replace_backs_up_old_content(ui) -> None:  # type: ignore[no-untyped-def]
    s, title = ui
    r = s.call("desktop-ui", "type", {"window": title, "name": "Nova Input",
                                      "text": "fresh", "replace": True})
    assert r.ok and value(s, title, name="Nova Input") == "fresh"
    [backup] = list((s.cfg.path("backups_dir") / "ui-text").glob("*.txt"))
    assert backup.read_text("utf-8") == "existing text"


def test_safe_click_acts_and_is_verified(ui) -> None:  # type: ignore[no-untyped-def]
    s, title = ui
    r = s.call("desktop-ui", "click", {"window": title, "name": "Nova Safe Button"})
    assert r.ok and r.data["method"] == "InvokePattern.Invoke"      # no coordinates
    assert value(s, title, automation_id="NovaStatus") == "safe clicked"


def test_dangerous_click_needs_approval(ui) -> None:  # type: ignore[no-untyped-def]
    s, title = ui
    with pytest.raises(ApprovalPending):
        s.call("desktop-ui", "click", {"window": title, "name": "Delete Everything"})
    assert value(s, title, automation_id="NovaStatus") == "idle"     # not pressed


def test_dangerous_keys_need_approval(ui) -> None:  # type: ignore[no-untyped-def]
    s, title = ui
    with pytest.raises(ApprovalPending):
        s.call("desktop-ui", "keys", {"window": title, "keys": "alt+f4"})
    assert any(w["title"] == title for w in s.call("desktop-ui", "windows", {}).data["windows"])
    r = focus_retry(lambda: s.call("desktop-ui", "keys", {"window": title, "keys": "ctrl+a"}))
    assert r.ok and r.evidence["window_still_open"]


def test_coordinates_are_red(ui) -> None:  # type: ignore[no-untyped-def]
    s, _ = ui
    with pytest.raises(ApprovalPending):
        s.call("desktop-ui", "click_xy", {"x": 10, "y": 10})


def test_focus_verified(ui) -> None:  # type: ignore[no-untyped-def]
    s, title = ui
    assert focus_retry(lambda: s.call("desktop-ui", "focus", {"window": title})).evidence[
        "foreground"]
