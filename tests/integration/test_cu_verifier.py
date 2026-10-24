"""Plan §72 CU-2 — the verifier never passes what it did not check. The three probes of
research Report B returned PASS before: a click with no evidence, a file with the WRONG
content, a Store-app "window" boolean. Now: checked → True/False; not checkable →
UNVERIFIED (never a PASS, never retried); screenshots can be kept on this PC."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from core.config import load_config
from core.queue.engine import TaskContext, Verdict, VerdictStatus
from core.queue.states import TaskState
from core.verifier import check_effect
from models.router import ProviderRouter
from providers.provider_base import HealthState, ProviderRequest
from tests.mocks.providers import FakeAdapter


def _check(skill: str, tool: str, params: dict[str, Any], ev: dict[str, Any],
           root: Path | None = None) -> tuple[bool | None, str]:
    resolve = (lambda raw: root / raw) if root else Path
    return check_effect({"skill": skill, "tool": tool, "params": params, "evidence": ev},
                        resolve)


# ------------------------------------------------------------ Report B's probes

def test_probe_click_without_evidence_is_not_a_pass() -> None:
    ok, proof = _check("desktop-ui", "click", {"window": "x", "name": "OK"}, {})
    assert ok is None and "যাচাই হয়নি" in proof
    ok, proof = _check("some-new-skill", "do", {}, {})
    assert ok is None and "যাচাইয়ের উপায় নেই" in proof           # was "(tool-confirmed)"


def test_probe_wrong_file_content_fails(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("WRONG CONTENT", encoding="utf-8", newline="")
    ok, proof = _check("files", "write", {"path": "a.txt", "content": "REQUIRED CONTENT",
                                          "mode": "overwrite"}, {"exists": True}, tmp_path)
    assert ok is False and "মেলেনি" in proof
    (tmp_path / "b.txt").write_text("REQUIRED CONTENT", encoding="utf-8", newline="")
    ok, _ = _check("files", "write", {"path": "b.txt", "content": "REQUIRED CONTENT"},
                   {}, tmp_path)
    assert ok is True
    (tmp_path / "c.txt").write_text("old\nnew line", encoding="utf-8", newline="")
    assert _check("files", "write", {"path": "c.txt", "content": "new line",
                                     "mode": "append"}, {}, tmp_path)[0] is True
    assert _check("files", "write", {"path": "c.txt", "content": "other",
                                     "mode": "append"}, {}, tmp_path)[0] is False
    assert _check("files", "write", {"path": "gone.txt", "content": "x"}, {},
                  tmp_path)[0] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows windows")
def test_probe_store_app_flag_alone_is_not_a_pass() -> None:
    # the worker's "window_titled" boolean is not trusted: no such window on screen
    ok, proof = _check("app-control", "open", {"app": "zzqx-no-such-app"},
                       {"window_titled": True, "running_pids": []})
    assert ok is False and "দেখা যাচ্ছে না" in proof


# ------------------------------------------------------------ app open / close

@pytest.mark.skipif(sys.platform != "win32", reason="Windows windows")
def test_app_open_needs_a_real_window() -> None:
    from tests.mocks.cu_lab import lab_app
    me = os.getpid()                                   # alive, but has no window
    ok, proof = _check("app-control", "open", {"app": "x"}, {"running_pids": [me]})
    assert ok is None and "window দেখা যাচ্ছে না" in proof
    ok, _ = _check("app-control", "open", {"app": "x"},
                   {"running_pids": [me], "still_starting": True})
    assert ok is None
    import psutil
    with lab_app("winforms") as title:
        from core.verifier.checks import visible_window_titles
        pid = next(p for p, t in visible_window_titles() or [] if t == title)
        assert psutil.pid_exists(pid)
        ok, proof = _check("app-control", "open", {"app": "x"}, {"running_pids": [pid]})
    assert ok is True and "window খোলা" in proof
    assert _check("app-control", "open", {"app": "x"}, {"running_pids": [999999]})[0] is False


def test_app_close_looks_again_itself() -> None:
    me = Path(sys.executable).name                      # this test's own program: running
    ok, _ = _check("app-control", "close", {"app": "x"},
                   {"still_running": [], "was_running": True, "process": [me]})
    assert ok is False                                  # the worker said "closed"; it is not
    ok, _ = _check("app-control", "close", {"app": "x"},
                   {"still_running": [], "was_running": True,
                    "process": ["zzqx-no-such-program.exe"]})
    assert ok is True
    ok, proof = _check("app-control", "close", {"app": "x"}, {"still_running": []})
    assert ok is None and "যাচাই হয়নি" in proof        # empty evidence proves nothing


def test_typed_text_is_read_back() -> None:
    assert _check("desktop-ui", "type", {"text": "hello"},
                  {"after": "say hello there"})[0] is True
    assert _check("desktop-ui", "type", {"text": "hello"}, {"after": "say"})[0] is False


# ------------------------------------------------------------ verdicts

def _operator_verdict(effects: list[dict[str, Any]]) -> Verdict:
    from core.orchestrator.executor import UserRequestExecutor
    ex = UserRequestExecutor.__new__(UserRequestExecutor)
    ex.skills = None
    return ex._check_operator({"calls": [{"skill": "x"}], "effects": effects}, True)


def test_operator_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.orchestrator.executor import UserRequestExecutor
    monkeypatch.setattr(UserRequestExecutor, "_resolve", lambda self, raw: tmp_path / raw)
    (tmp_path / "ok.txt").write_text("hi", encoding="utf-8")
    good = {"skill": "files", "tool": "write", "params": {"path": "ok.txt", "content": "hi"}}
    click = {"skill": "desktop-ui", "tool": "click", "params": {}, "evidence": {}}
    wrong = {"skill": "files", "tool": "write", "params": {"path": "ok.txt", "content": "no"}}
    assert _operator_verdict([good]).final is VerdictStatus.PASS
    v = _operator_verdict([good, click])
    assert v.final is VerdictStatus.INCONCLUSIVE and not v.passed and v.redo_from is None
    assert _operator_verdict([good, click, wrong]).final is VerdictStatus.NEEDS_WORK


class _Unverifiable:
    async def plan(self, ctx: TaskContext) -> list[str]:
        return ["do"]

    async def run(self, ctx: TaskContext) -> str:
        return "clicked Save"

    async def verify(self, ctx: TaskContext, result: str) -> Verdict:
        return Verdict(False, "could not verify: desktop-ui.click", VerdictStatus.INCONCLUSIVE)


def test_unverified_task_ends_honestly_and_is_not_retried(tmp_path: Path) -> None:
    from tests.mocks.tasks import make_task_env
    env = make_task_env(tmp_path / "a.db", tmp_path / "bk")
    env.engine.register("user_request", _Unverifiable())
    t = env.store.create(title="x", request_text="x", task_type="user_request",
                         channel="telegram", chat_id="555")
    asyncio.run(env.engine.run_once())
    done = env.store.get(t.id)
    assert done.state is TaskState.COMPLETED and done.retry_count == 0
    msg = next(m for _, m in env.sent if f"TASK #{t.id}" in m)
    assert "যাচাই করা যায়নি" in msg and "✅" not in msg and "COMPLETE" not in msg


# ------------------------------------------------------------ screenshot privacy

def _router(privacy: str) -> tuple[ProviderRouter, dict[str, FakeAdapter]]:
    cfg = load_config()
    caps = frozenset({"vision"})
    ad = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in cfg.providers.providers}
    ad["ollama_local"] = FakeAdapter("ollama_local", state=HealthState.UNAVAILABLE,
                                     capabilities=caps)
    ad["gemini_api"] = FakeAdapter("gemini_api", [json.dumps({"seen": "cloud"})],
                                   capabilities=caps)
    providers = cfg.providers.model_copy(update={"screenshot_privacy": privacy})
    return ProviderRouter(providers, ad), ad


def test_screenshots_can_be_kept_on_this_pc() -> None:
    req = ProviderRequest(task_id=1, task_type="vision", user_request="what is on screen",
                          images=(b"png",))
    router, ad = _router("local_only")                 # the local model is down
    r = asyncio.run(router.complete(req))
    assert not r.ok and "LOCAL_ONLY" in (r.error or "")
    assert not ad["gemini_api"].requests                   # the screenshot never left the PC
    router, ad = _router("local_first")                # owner's choice 2026-09-25
    assert asyncio.run(router.complete(req)).ok and ad["gemini_api"].requests
    # an explicit per-request rule works for any request, with or without images
    router, ad = _router("local_first")
    r = asyncio.run(router.complete(ProviderRequest(
        task_id=1, task_type="vision", user_request="x", privacy="local_only")))
    assert not r.ok and not ad["gemini_api"].requests
