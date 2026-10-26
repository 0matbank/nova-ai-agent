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
    assert d.fixes() == {} and told == []
    assert _learn(learner, "আমার পিছি চালু আছে") == [("পিছি", "পিসি")]
    assert d.fixes() == {"পিছি": "পিসি"}
    assert d.store.rows("speech_fix")["পিছি"][1] == EDITED
    assert "«পিছি»" in told[0] and "«পিসি»" in told[0] and "মুছো" in told[0]
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
