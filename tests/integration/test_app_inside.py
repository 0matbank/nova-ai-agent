"""About an app that is open (owner 2026-10-23, real phone sentences): "OBS এখন কি অবস্থা /
কি দেখছ" re-opened OBS or got a web answer; "OBS তো open আছে, update চাইছে, skip করো"
got the process list. Now: look → app-control.view (reads ANY app's windows, read-only);
work inside an open app → the Windows Operator — never a fixed rule per app or button
(plan §71)."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psutil
import pytest

from core.queue.engine import VerdictStatus
from core.router.intent import classify_by_rules, is_quick_request
from tests.unit.test_app_resolver import resolver

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers" / "desktop-worker"))

VIEW = [
    ("OBS অপেন করা এবং কি আছে সেটি আমাকে জানা", True),                 # owner, phone
    ("OBS এখন কি তাছে, সেটি আমাকে জানাও, কি অবস্থা এখন OBS এর?", False),   # owner, phone
    ("Amii bolasii obs ar Akhon kii Obostha seti amk janau", False),       # owner, text
    ("আমি তুমাকে বলে সিজে, এখন যে OBS অপেন হোয়ে সে, এখন OBS অপেরে তুমি কি কি দেখতে বাছো",
     False),                                                               # owner, phone
    ("what do you see in obs", False),
    ("capcut e ki dekhacche", False),
]
INSIDE = [
    "OBS এখন অপেন আছে, OBS এখন অপ্রিট চাছে, তুমি এটাগে স্কিপ করা",            # owner, phone
    "আমি তুমাকে বলে সিদে, OBS তো অপেন আসে, OBS এটি short interface অপেন হয়েছে, সে ইখানে "
    "update দিতে বলছে, তুমি সেটাকে update না দিয়ে skip করা",                  # owner, phone
    "capcut to open ache, export koro",
]


@pytest.mark.parametrize(("text", "first"), VIEW)
def test_look_inside_an_app(tmp_path: Path, text: str, first: bool) -> None:
    apps = resolver(tmp_path)
    hit = classify_by_rules(text, apps, "c")
    assert hit is not None and hit.skill is not None, text
    assert hit.skill[:2] == ("app-control", "view"), hit
    assert hit.skill[2]["open_first"] is first                   # "তো open আছে" ≠ open it
    assert is_quick_request(text, apps)                          # no AI, the fast lane
    assert apps.is_low_risk(text)                                # voice: reading only


@pytest.mark.parametrize("text", INSIDE)
def test_work_inside_an_open_app_goes_to_the_operator(tmp_path: Path, text: str) -> None:
    apps = resolver(tmp_path)
    hit = classify_by_rules(text, apps, "c")
    assert hit is not None and hit.category == "pc_control" and hit.skill is None, hit
    assert hit.meta["inside_app"] in ("obs", "capcut")        # never re-opened, no fixed skill
    assert not is_quick_request(text, apps)                   # AI work → the normal lane
    assert not apps.is_low_risk(text)                         # acting keeps the strict check


def test_what_stays_the_same(tmp_path: Path) -> None:
    apps = resolver(tmp_path)
    for text, tool in [("obs open koro", "open"), ("obs bondho koro", "close"),
                       ("প্রিমিয়ার প্রো খোলো", "open")]:
        hit = classify_by_rules(text, apps, "c")
        assert hit is not None and hit.skill is not None and hit.skill[1] == tool, text
    # a screen question stays a screen question; "error" is no app ("winrar" sound-alike)
    hit = classify_by_rules("screen-e ki error dekhacche", apps, "c")
    assert hit is not None and hit.category == "screenshot"
    assert apps.inside_command("ki error dekhacche") is None
    assert apps.inside_command("D:\\a.mp4 file ta dekho") is None
    # the rules alone saw only "খোলো" — a known app still takes the fast lane
    assert is_quick_request("প্রিমিয়ার প্রো খোলো", apps)


def test_prompt_shape() -> None:
    import desktop_inside as di
    buttons = ["Update Now", "Remind me Later", "Skip Version", "Close"]
    assert di.is_prompt("OBSUpdate", "OBSApp.OBSUpdate", ["New update"], buttons)
    assert not di.is_prompt("QDockWidget", "OBSApp.OBSBasic.scenesDock", [], ["Add"])
    assert not di.is_prompt("Frame", "", ["text"], ["Close", "Minimize"])


# ------------------------------------------------------------- the real desktop

@contextmanager
def private_app(buttons: str) -> Iterator[tuple[str, int]]:
    """A private app + its owned pop-up (tests/mocks/uia_popup.ps1) — never the owner's."""
    import uiautomation as auto
    title = f"Nova Popup Test {uuid.uuid4().hex[:8]}"
    exe = shutil.which("powershell")
    assert exe
    script = Path(__file__).resolve().parents[1] / "mocks" / "uia_popup.ps1"
    proc = subprocess.Popen([exe, "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",  # noqa: S603
                             "-File", str(script), "-Title", title, "-Buttons", buttons])
    try:
        with auto.UIAutomationInitializerInThread(debug=False):
            deadline = time.time() + 20
            # UIA puts a window's own dialogs under it (as with OBS's update dialog)
            while not auto.WindowControl(searchDepth=2, Name=f"{title} Dialog").Exists(0, 0):
                assert time.time() < deadline, "test pop-up did not appear"
                time.sleep(0.2)
        yield title, proc.pid
    finally:
        with contextlib.suppress(psutil.NoSuchProcess):
            psutil.Process(proc.pid).kill()
        proc.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")
@pytest.mark.parametrize("buttons", ["Update Now|Remind me Later|Skip Version",
                                     "Yes|No", "এখন নয়|আপডেট করুন"])
def test_any_apps_popup_is_read(monkeypatch: pytest.MonkeyPatch, buttons: str) -> None:
    """Whatever the buttons are called, the owned dialog is reported as the pop-up with
    its text and buttons; the main window is not a pop-up."""
    pytest.importorskip("uiautomation")
    import desktop_inside as di
    with private_app(buttons) as (title, pid):
        monkeypatch.setattr(di, "_app_pids", lambda name: (
            "testapp", {"name": "Test App", "process": ["powershell.exe"]}, {pid}))
        seen = di.view("testapp")
        assert seen["running"]
        assert [w["title"] for w in seen["windows"]] == [title]
        (p,) = seen["prompts"]
        assert p["title"] == f"{title} Dialog"
        assert p["buttons"] == buttons.split("|")
        assert "There is a new update available:" in p["texts"]


# ---------------------------------------------------------------- verifier

def _verdict(skill: str, answer: str, ev: dict[str, Any]) -> Any:
    from core.orchestrator.executor import UserRequestExecutor
    ex = UserRequestExecutor.__new__(UserRequestExecutor)
    return ex._check_skill({"skill": skill, "ok": True, "answer": answer}, ev, True, "")


def test_verifier() -> None:
    ev = {"running": True, "windows": ["OBS 32.0.4 - Profile"], "prompts": ["New update"]}
    good = _verdict("app-control.view",
                    "OBS খোলা আছে।\n• Window: OBS 32.0.4 - Profile\n• Pop-up: «New update»", ev)
    assert good.status is VerdictStatus.PASS
    bad = _verdict("app-control.view", "OBS খোলা আছে।", ev)
    assert bad.status is VerdictStatus.NEEDS_WORK
    closed = _verdict("app-control.view", "OBS এখন খোলা নেই।", {"running": False})
    assert closed.status is VerdictStatus.LIMITED


# ------------------------------------------------------------ CU-5: compound requests

@pytest.mark.parametrize(("text", "app"), [
    ("OBS খোলো তারপর recording শুরু করো", "obs"),
    ("audacity kholo tarpor ekta notun project banao", "audacity"),
    ("open capcut and then import the clip", "capcut"),
    ("প্রিমিয়ার প্রো খোলো, এরপর নতুন project বানাও", "premiere-pro"),
])
def test_open_then_more_keeps_the_whole_goal(tmp_path: Path, text: str, app: str) -> None:
    apps = resolver(tmp_path)
    hit = classify_by_rules(text, apps, "c")
    assert hit is not None and hit.skill is None, hit             # not just "open"
    assert hit.meta == {"inside_app": app, "compound": True}
    assert not is_quick_request(text, apps)                        # multi-step: normal lane


def test_plain_open_is_still_the_fast_path(tmp_path: Path) -> None:
    apps = resolver(tmp_path)
    for text in ("obs open koro", "audacity kholo", "OBS খোলো তারপর"):   # nothing after
        hit = classify_by_rules(text, apps, "c")
        assert hit is not None and hit.skill is not None and hit.skill[1] == "open", text


def test_exact_app_commands_use_no_ai_at_all(tmp_path: Path) -> None:
    """CU-5 (Report F phase 4): after speech recognition, an exact installed-app command
    is decided by rules — zero AI calls (fast, and nothing leaves the PC)."""
    import asyncio

    from core.config import load_config
    from core.router.intent import IntentRouter
    from models.router import ProviderRouter
    from providers.provider_base import HealthState
    from tests.mocks.providers import FakeAdapter
    cfg = load_config()
    ad = {n: FakeAdapter(n, state=HealthState.HEALTHY) for n in cfg.providers.providers}
    router = IntentRouter(ProviderRouter(cfg.providers, ad), resolver(tmp_path))
    for text in ("প্রিমিয়ার প্রো খোলো", "obs open koro", "audacity bondho koro",
                 "OBS খোলো তারপর recording শুরু করো", "কোন কোন অ্যাপ খোলা আছে"):
        hit = asyncio.run(router.classify(text, "t", "c"))
        assert hit.source == "rule", (text, hit)
    assert not any(a.requests for a in ad.values())
