"""V1.1 (owner 2026-10-16): "কোন কোন software/app/window খোলা আছে" lists the apps open
on the screen (windows.open_windows) — not the process list, not the PC status."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from core.queue.engine import VerdictStatus
from core.router.intent import classify_by_rules, is_open_windows_question
from skills.windows.tools.open_windows import describe

OPEN_WINDOWS = [
    "এই মুহূর্তে PC-তে কোন কোন software/app/window খোলা আছে।",           # owner, phone
    "Pc ta Akhon kii software Chalu asa kii hossa pc ta ki ki Open asa",   # owner, phone
    "কোন কোন অ্যাপ খোলা আছে?",
    "কী কী খোলা আছে",
    "কি কি ওপেন আছে",
    "pc te ki ki app open ache",
    "ki ki software chalu ache",
    "kon kon window khola ache",
    "which apps are open right now?",
    "what windows are open",
    "show open windows",
]
PROCESSES = ["কি কি process চলছে", "background e ki ki process cholche",
             "show the process list", "ki ki process cholche dekhao"]
STATUS = ["PC er obostha ki", "পিসির অবস্থা কি", "what is my PC status?"]


@pytest.mark.parametrize("text", OPEN_WINDOWS)
def test_open_windows_questions(text: str) -> None:
    hit = classify_by_rules(text)
    assert hit is not None and hit.skill == ("windows", "open_windows", {}), hit


@pytest.mark.parametrize("text", PROCESSES)
def test_process_questions_stay_processes(text: str) -> None:
    hit = classify_by_rules(text)
    assert not is_open_windows_question(text)
    assert hit is not None and hit.skill == ("windows", "processes", {}), (text, hit)


@pytest.mark.parametrize("text", STATUS)
def test_status_questions_stay_status(text: str) -> None:
    hit = classify_by_rules(text)
    assert hit is not None and hit.skill == ("windows", "status", {}), hit


@pytest.mark.parametrize("text", ["notepad kholo", "chrome বন্ধ করো", "vs code open koro"])
def test_app_commands_are_not_questions(text: str) -> None:
    assert not is_open_windows_question(text)


def test_describe_groups_by_app() -> None:
    apps = [{"name": "Google Chrome", "foreground": True, "windows": [
                {"title": "YouTube - Google Chrome"}, {"title": "Gmail - Google Chrome"}]},
            {"name": "Visual Studio Code", "foreground": False, "windows": [
                {"title": "app.py - Nova - Visual Studio Code"}]}]
    text = describe(apps)
    assert text.startswith("2 app(s) open:")
    assert "• Google Chrome (সামনে) — 2 window: YouTube - Google Chrome; Gmail" in text
    assert "• Visual Studio Code: app.py" in text
    assert describe([]) == "No app window is open on the screen."


def test_friendly_names() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers" / "desktop-worker"))
    import desktop_windows as dw
    assert dw.friendly_name("chrome.exe", "", "x", {}) == "Google Chrome"
    assert dw.friendly_name("Adobe Premiere Pro.exe", "", "x",
                            {"adobe premiere pro.exe": "Adobe Premiere Pro 2026"}) == \
        "Adobe Premiere Pro 2026"
    # Store apps are hosted by ApplicationFrameHost: the window title names them
    assert dw.friendly_name("ApplicationFrameHost.exe", "", "Calculator", {}) == "Calculator"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop")
def test_real_window_is_listed_and_background_processes_are_not() -> None:
    """A private test window (never the owner's apps) shows up; a background-only
    process (this Python test run has no window) does not."""
    pytest.importorskip("uiautomation")
    from tests.mocks.uia_window import uia_test_window
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers" / "desktop-worker"))
    import desktop_windows as dw
    with uia_test_window() as title:
        r = dw.visible_apps()
    titles = [w["title"] for a in r["apps"] for w in a["windows"]]
    assert title in titles
    names = {a["process"].lower() for a in r["apps"]}
    assert not names & {"llama-server.exe", "msmpeng.exe", "python.exe", "ollama.exe"}
    assert r["windows"] == len(titles)


# ------------------------------------------------------------------ verifier

class _Ctx:
    def __init__(self, text: str) -> None:
        from types import SimpleNamespace
        self.task = SimpleNamespace(request_text=text, id=1, chat_id="c")


def _verdict(skill: str, answer: str, ev: dict[str, Any], text: str) -> Any:
    from core.orchestrator.executor import UserRequestExecutor
    ex = UserRequestExecutor.__new__(UserRequestExecutor)
    return ex._check_skill({"skill": skill, "ok": True, "answer": answer}, ev, True, text)


def test_verifier_passes_only_with_real_app_names() -> None:
    q = "কোন কোন app খোলা আছে"
    ok = _verdict("windows.open_windows", "2 app(s) open:\n• Google Chrome\n• Notepad",
                  {"apps": ["Google Chrome", "Notepad"]}, q)
    assert ok.passed and ok.status is VerdictStatus.PASS
    missing = _verdict("windows.open_windows", "some apps are open",
                       {"apps": ["Google Chrome"]}, q)
    assert not missing.passed and missing.status is VerdictStatus.NEEDS_WORK
    empty = _verdict("windows.open_windows", "No app window is open on the screen.",
                     {"apps": []}, q)
    assert empty.status is VerdictStatus.LIMITED                  # never a plain PASS
    wrong = _verdict("windows.processes", "15 process(es); top: llama-server.exe",
                     {}, q)
    assert not wrong.passed and wrong.status is VerdictStatus.NEEDS_WORK


def test_open_windows_task_end_to_end(tmp_path: Path) -> None:
    """The owner's phone sentence → the open_windows skill through the task engine,
    with a fake Desktop Worker answering."""
    from core.orchestrator.executor import UserRequestExecutor
    from core.queue.states import TaskState
    from core.router.intent import IntentRouter
    from tests.mocks.skills import make_skill_env

    class FakeDesktop:
        async def call(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
            assert path == "/v1/apps/visible"
            return {"ok": True, "windows": 2, "apps": [
                {"name": "Google Chrome", "process": "chrome.exe", "foreground": True,
                 "windows": [{"title": "YouTube - Google Chrome", "minimized": False}]},
                {"name": "Visual Studio Code", "process": "Code.exe", "foreground": False,
                 "windows": [{"title": "Nova - Visual Studio Code", "minimized": False}]}]}

    s = make_skill_env(tmp_path, workers={"desktop": FakeDesktop()})
    s.tasks.engine.register("user_request", UserRequestExecutor(
        IntentRouter(None), None, s.runner))   # type: ignore[arg-type]
    text = "এই মুহূর্তে PC-তে কোন কোন software/app/window খোলা আছে।"
    tid = int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                   channel="telegram", chat_id="555").id)
    for _ in range(4):
        if not asyncio.run(s.tasks.engine.run_once()):
            break
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t
    assert "Google Chrome" in t.result_summary and "Visual Studio Code" in t.result_summary
    assert "process" not in t.result_summary
    done = next(m for _, m in s.tasks.sent if f"TASK #{tid} COMPLETE" in m)
    assert "PASS" in done and "windows.open_windows" not in done or "screen-এ 2টা" in done
