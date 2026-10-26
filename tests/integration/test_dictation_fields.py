"""V1.2 dictation into real fields (owner 2026-10-26: "লিখতে পারল না"). Nova's own lab
page in Edge (a private profile, never the owner's browser), through the real Desktop
Worker: a textarea and a chat/mail-style editor take the words at the cursor; with
focus on the bare page nothing is typed (a page shortcut like YouTube's "k" must never
be pressed) and the owner is told plainly, once."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from core.queue.engine import TaskBlocked
from tests.mocks.desktop import real_desktop_client
from tests.mocks.skills import make_skill_env

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")

WORDS = "নোভা লিখছে ok"


def _lab(focus: str):  # type: ignore[no-untyped-def]
    from tests.mocks.cu_lab import lab_app
    return lab_app(f"fields:{focus}")


def _front(title: str) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers" / "desktop-worker"))
    import desktop_ui
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread(debug=False):
        desktop_ui._activate(auto.WindowControl(searchDepth=1, Name=title))
    time.sleep(0.6)


def _state(title: str) -> dict[str, str]:
    """The lab page's own state line «ta:… |ce:… |k:…» (UIA cannot read a
    contenteditable box, so the page reports what it holds)."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread(debug=False):
        w = auto.WindowControl(searchDepth=1, Name=title)
        for c, _ in auto.WalkControl(w, maxDepth=14):
            name = str(c.Name or "")
            if name.startswith("ta:"):
                parts = dict(p.split(":", 1) for p in name.split("|"))
                return {k: v.replace("\xa0", " ").strip() for k, v in parts.items()}
    return {}


def _dictate(tmp_path: Path, title: str):  # type: ignore[no-untyped-def]
    s = make_skill_env(tmp_path, {"desktop": real_desktop_client(tmp_path)})
    r = None
    for _ in range(3):           # Windows may refuse the foreground while the owner types
        _front(title)
        r = s.call("desktop-ui", "dictate", {"text": WORDS, "window": title})
        if r.ok or r.evidence.get("needs_owner") or "user_active" not in r.summary:
            return r
        time.sleep(1.5)
    return r


@pytest.mark.parametrize("focus", ["ta", "ce"])
def test_words_land_at_the_cursor_of_a_real_field(tmp_path: Path, focus: str) -> None:
    with _lab(focus) as title:
        r = _dictate(tmp_path, title)
        assert r.ok, r.summary
        time.sleep(0.3)
        assert _state(title)[focus] == f"start {WORDS}"
        # a field that can be read back is watched for the owner's fix; an editor cannot be
        assert bool(r.data.get("watch")) is (focus == "ta")


def test_the_bare_page_is_never_typed_into(tmp_path: Path) -> None:
    with _lab("page") as title:
        r = _dictate(tmp_path, title)
        assert not r.ok and r.evidence.get("needs_owner"), r.summary
        assert r.summary.startswith("⌨️ লিখিনি:") and title in r.summary
        assert _state(title) == {"ta": "start", "ce": "start", "k": "off"}   # untouched


def test_a_task_that_needs_the_owner_is_not_retried() -> None:
    """The executor turns "needs the owner" into one plain message, no 3× retry."""
    from core.orchestrator.executor import UserRequestExecutor
    from core.router.intent import Intent, IntentRouter
    from core.skills.api import ToolResult
    from tests.integration.test_answer_routing import router

    class Runner:
        async def invoke(self, ctx, skill, tool, params):  # type: ignore[no-untyped-def]
            return ToolResult(False, "⌨️ লিখিনি: «x» — cursor কোনো লেখার ঘরে নেই।", {},
                              {"needs_owner": True})

    ex = UserRequestExecutor(IntentRouter(router()), router(), None)
    ex.skills = Runner()  # type: ignore[assignment]
    with pytest.raises(TaskBlocked, match="লিখিনি"):
        asyncio.run(ex._run_skill(None, Intent(  # type: ignore[arg-type]
            "pc_control", 0.95, "rule", ("desktop-ui", "dictate", {"text": "x"}))))


def _owner_fixes(old: str, new: str) -> None:
    """What the owner does: fix a word in the field Nova just typed into."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread(debug=False):
        v = auto.GetFocusedControl().GetValuePattern()
        value = str(v.Value)
        i = value.rfind(old)
        v.SetValue(value[:i] + new + value[i + len(old):])


def test_the_owners_own_fix_in_a_real_field_is_learned(tmp_path: Path) -> None:
    """Atticus item 3 (owner 2026-10-26): Nova types «আমার পিছি চালু আছে», the owner fixes
    «পিছি» to «পিসি» in the field; the real worker reports only the typed words, and the
    same fix seen twice goes into the Speech Dictionary — the owner is told."""
    from core.ipc.client import WorkerError
    from core.voice.dictionary import SpeechDictionary
    from core.voice.edits import EditLearner
    heard = "আমার পিছি চালু আছে"
    client = real_desktop_client(tmp_path)
    d = SpeechDictionary()
    told: list[str] = []

    async def peek(token: str) -> tuple[str | None, str | None]:
        r = await client.call("POST", "/v1/ui/dictated", json={"token": token})
        return r.get("span"), r.get("ended")

    async def tell(chat: str, text: str) -> None:
        told.append(text)
    learner = EditLearner(d, peek, tell)

    async def once(title: str) -> list[tuple[str, str]]:
        for _ in range(3):           # Windows may refuse the foreground while the owner types
            _front(title)
            try:
                r = await client.call("POST", "/v1/ui/dictate",
                                      json={"text": heard, "window": title})
                break
            except WorkerError as e:
                assert "user_active" in str(e), e
                await asyncio.sleep(1.5)
        assert r["verified"] and r["watch"], r
        job = asyncio.ensure_future(learner.learn("555", heard, r["watch"]))
        await asyncio.sleep(1.0)
        await asyncio.to_thread(_owner_fixes, "পিছি", "পিসি")
        return await job

    async def go(title: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict]:
        first = await once(title)
        second = await once(title)
        return first, second, await client.call("POST", "/v1/ui/dictated",
                                                json={"token": "0" * 16})
    with _lab("ta") as title:
        first, second, stale = asyncio.run(go(title))
        assert _state(title)["ta"].endswith("আমার পিসি চালু আছে")
    assert first == [] and second == [("পিছি", "পিসি")]
    assert d.apply("পিছির অবস্থা বলো").text == "পিসির অবস্থা বলো"
    assert len(told) == 2 and told[0].startswith("📝")             # noticed, then learned
    assert "«পিছি»" in told[1] and "«পিসি»" in told[1] and "শিখলাম" in told[1]
    assert stale["span"] is None and stale["ended"]              # unknown watch: nothing read
