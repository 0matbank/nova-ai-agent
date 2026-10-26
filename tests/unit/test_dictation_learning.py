"""V1.2 learning from the owner's own fixes after dictation (Atticus item 3): only an
edit that sounds like a mishearing counts, the same fix twice is learned, the owner is
told — a changed meaning, number or «না» never teaches anything."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from core.voice.dictionary import EDITED, SpeechDictionary, sounds_alike
from core.voice.edits import EditLearner, edited_words

# measured 2026-10-26 (owner recordings and common Whisper slips): a mishearing fixed
MISHEARD = [("পিছি", "পিসি"), ("সনাও", "শোনাও"), ("পিসি", "PC"), ("নুভা", "নোভা"),
            ("বড্ধমন", "বর্তমান"), ("বট্তোমন", "বর্তমান"), ("ফেসবুক", "Facebook"),
            ("নোভা", "Nova"), ("স্ট্যাটাস", "status"), ("ক্লোড", "Claude"),
            ("ওপেন এআই", "OpenAI"), ("ভিএস কোড", "VS Code"), ("ডেক্সটপ", "ডেস্কটপ")]
# the owner changed what they meant: never a dictionary fix
NEW_IDEA = [("কাল", "কাজ"), ("ঢাকা", "রাজশাহী"), ("আজ", "কাল"), ("হ্যাঁ", "না"),
            ("ভালো", "খারাপ"), ("মিটিং", "ডিনার"), ("সোমবার", "মঙ্গলবার"), ("কিনব", "বেচব"),
            ("দুই", "তিন"), ("আসব", "যাব"), ("দেখা", "লেখা"), ("আম", "জাম"),
            ("বলো", "খোলো"), ("মামা", "চাচা"), ("কাকা", "কাকি"), ("৫টায়", "৬টায়"),
            ("করেছি", "করিনি")]


@pytest.mark.parametrize(("heard", "meant"), MISHEARD)
def test_a_fixed_mishearing_sounds_alike(heard: str, meant: str) -> None:
    assert sounds_alike(heard, meant)


@pytest.mark.parametrize(("heard", "meant"), NEW_IDEA)
def test_a_changed_meaning_does_not(heard: str, meant: str) -> None:
    assert not sounds_alike(heard, meant)


def _peek(spans: list[tuple[str | None, str | None]]):  # type: ignore[no-untyped-def]
    it = iter(spans)

    async def peek(token: str) -> tuple[str | None, str | None]:
        return next(it, spans[-1])
    return peek


def _watch(spans, typed="আমার পিছি চালু আছে"):  # type: ignore[no-untyped-def]
    return asyncio.run(edited_words(_peek(spans), "t", typed, seconds=0.5, poll=0.01,
                                    settled=0.05))


def test_the_settled_edit_is_taken_not_a_half_typed_word() -> None:
    typing = [("আমার পিছি চালু আছে", None), ("আমার পি চালু আছে", None),
              ("আমার পিস চালু আছে", None), ("আমার পিসি চালু আছে", None)]
    assert _watch(typing) == "আমার পিসি চালু আছে"


def test_a_fix_then_send_still_counts_and_no_edit_is_nothing() -> None:
    sent = [("আমার পিসি চালু আছে", None), (None, "the text around the words changed")]
    assert _watch(sent) == "আমার পিসি চালু আছে"
    assert _watch([("আমার পিছি চালু আছে", None)]) is None
    assert _watch([(None, "focus moved")]) is None


def _learner(spans):  # type: ignore[no-untyped-def]
    told: list[str] = []

    async def tell(chat: str, text: str) -> None:
        told.append(text)
    d = SpeechDictionary()
    learner = EditLearner(d, _peek(spans), tell, poll=0.01, settled=0.05)
    return d, learner, told


def _learn(learner, typed: str) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return asyncio.run(learner.learn("555", typed, "t"))


def test_the_same_fix_twice_is_learned_and_the_owner_is_told() -> None:
    d, learner, told = _learner([("আমার পিসি চালু আছে", None)])
    assert _learn(learner, "আমার পিছি চালু আছে") == []          # once can be a one-off
    assert d.fixes() == {}
    assert told == ["📝 আপনার সংশোধন দেখলাম: «পিছি» → «পিসি»। আরেকবার একই ভুল ঠিক "
                    "করলে শিখে নেব।"]
    assert _learn(learner, "আমার পিছি চালু আছে") == [("পিছি", "পিসি")]
    assert d.fixes() == {"পিছি": "পিসি"}
    assert d.store.rows("speech_fix")["পিছি"][1] == EDITED
    assert "«পিছি»" in told[1] and "«পিসি»" in told[1] and "মুছো" in told[1]
    assert len(told) == 2
    assert d.apply("পিছির অবস্থা বলো").text == "পিসির অবস্থা বলো"
    assert "আপনার সংশোধন থেকে শেখা" in d.listing()


def test_a_changed_meaning_teaches_nothing_however_often() -> None:
    d, learner, told = _learner([("কাল সকাল ছয়টায় মিটিং", None)])
    for _ in range(3):
        assert _learn(learner, "কাল সকাল পাঁচটায় মিটিং") == []
    d2, learner2, _ = _learner([("আজ সকালে যাব না", None)])
    for _ in range(3):
        assert _learn(learner2, "আজ সকালে যাব") == []
    assert d.fixes() == {} and d2.fixes() == {} and told == []


def test_only_a_dictation_with_a_watch_starts_learning() -> None:
    _d, learner, _told = _learner([("x", None)])

    async def go() -> int:
        learner.after_skill("555", ("desktop-ui", "click"), {}, {"watch": "t"}, True)
        learner.after_skill("555", ("desktop-ui", "dictate"), {"text": "x"}, {}, True)
        learner.after_skill("555", ("desktop-ui", "dictate"), {"text": "x"},
                            {"watch": "t"}, False)
        n = len(learner._jobs)
        learner.after_skill("555", ("desktop-ui", "dictate"), {"text": "x"},
                            {"watch": "t"}, True)
        for job in list(learner._jobs):
            job.cancel()
        return n
    assert asyncio.run(go()) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="the Desktop Worker runs on Windows")
def test_the_worker_finds_the_typed_words_by_what_stands_around_them() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers" / "desktop-worker"))
    import desktop_ui
    around = desktop_ui._around
    assert around("Hello  world", "Hello আমার পিছি world", "আমার পিছি ") is not None
    assert around("ab", "abb", "b") == ("ab", "")                 # same letters: same words
    assert around("start ", "start নোভা", "নোভা") == ("start ", "")
    assert around("a\r\nb", "a\r\nনোভা\r\nb", "নোভা\n") == ("a\n", "b")
    assert around("x", "y", "নোভা") is None                       # not what was typed
    assert around(None, "নোভা", "নোভা") is None                   # unreadable: no watch


# ---------------------------------------------------------- the owner's test, 2026-10-26

TYPED_157 = ("আমার সাথে জখন কাস করবে, তখন খোল দ্রুত কাস পারার চেষ্টা করবে, ধীর কোতিতে কাস "
             "করবে না ঠিক আসে?")
FIXED_157 = ("আমার সাথে যখন কাজ করবে, তখন খুব দ্রুত কাজ করার চেষ্টা করবে, ধীর গতিতে কাজ "
             "করবে না ঠিক আছে?")


def test_several_words_fixed_in_one_sentence_are_noticed_then_learned() -> None:
    """Task #157: the owner fixed six words at once — the old rule took a sentence with
    more than two changes for a new request and learned nothing, said nothing."""
    d, learner, told = _learner([(FIXED_157, None)])
    assert _learn(learner, TYPED_157) == []                      # once: noticed, told
    assert len(told) == 1 and told[0].startswith("📝") and "«কাস» → «কাজ»" in told[0]
    learned = _learn(learner, TYPED_157)                         # the second time
    assert ("কাস", "কাজ") in learned and ("জখন", "যখন") in learned
    assert ("খোল", "খুব") not in learned and ("পারা", "করা") not in learned  # other words
    assert d.apply("আমার সাথে জখন কাস করবে ঠিক আসে").text == "আমার সাথে যখন কাজ করবে ঠিক আছে"


def test_a_rewritten_sentence_still_teaches_nothing() -> None:
    _d, learner, told = _learner([("কাল অফিসে থাকব না", None)])
    for _ in range(2):
        assert _learn(learner, "আজ বাজারে যাব") == []
    assert told == []


def _dictate_tool():  # type: ignore[no-untyped-def]
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "skills" / "desktop-ui" / "tools" / "dictate.py"
    spec = importlib.util.spec_from_file_location("dictate_tool", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_tool(route):  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from tests.mocks.desktop import FakeDesktop
    tool = _dictate_tool()
    env = SimpleNamespace(workers={"desktop": FakeDesktop({"/v1/ui/dictate": route})},
                          task_id=1)
    return asyncio.run(tool.run(tool.P(text="আমার পিসি"), env))  # type: ignore[arg-type]


def test_owner_still_busy_after_waiting_is_told_plainly_and_not_retried() -> None:
    """Tasks #155/#158 failed with a raw English error: the owner had just released the
    dictation hotkey. Now the worker waits for quiet; if the owner keeps going, one
    plain Bangla message and no retry."""
    from core.ipc.client import WorkerError
    r = _run_tool(WorkerError('desktop: HTTP 422 {"detail":"user_active: the owner is '
                              'using the keyboard/mouse"}'))
    assert not r.ok and r.evidence["needs_owner"]
    assert r.summary.startswith("⌨️ লিখিনি") and "Ctrl+Alt+D" in r.summary


def test_typed_but_not_found_in_the_field_is_never_typed_again() -> None:
    """Task #158: typed, the check failed, and the task tried to type it again — the
    words could have landed twice. Now: say so once, never retry."""
    r = _run_tool({"window": {"title": "নোট"}, "readable": True, "verified": False,
                   "check": {"before": 3, "after": 5, "typed": 9, "missing_lines": [0]}})
    assert not r.ok and r.evidence["needs_owner"] and "দুবার" in r.summary
    ok = _run_tool({"window": {"title": "নোট"}, "readable": False, "verified": False})
    assert ok.ok                              # an editor that cannot be read back: typed


@pytest.mark.skipif(sys.platform != "win32", reason="the Desktop Worker runs on Windows")
def test_dictation_waits_for_the_owners_hands_to_be_still(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import time
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers" / "desktop-worker"))
    import desktop_guard
    from desktop_ui import UIError
    monkeypatch.setattr(desktop_guard.ESCAPE, "arm", lambda: False)
    monkeypatch.setitem(desktop_guard._state, "halted_at", 0.0)
    monkeypatch.setitem(desktop_guard._state, "owner_at", time.monotonic() - 1.0)
    with pytest.raises(UIError, match="user_active"):
        desktop_guard.require_owner_idle()                  # other actions: refuse at once
    t0 = time.monotonic()
    desktop_guard.require_owner_idle(wait=3.0)               # dictation: waits for quiet
    assert 0.3 < time.monotonic() - t0 < 1.5
