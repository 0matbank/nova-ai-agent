"""V1.2 Speech Dictionary: the owner's words and learned fixes, applied to a voice
transcript before routing; taught by fixed commands or by confirming twice."""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import pytest

from channels.base import IncomingCallback, IncomingMessage
from core.orchestrator.commands import CommandRouter, HealthSources
from core.voice.dictionary import (
    DbStore,
    SpeechDictionary,
    dictionary_command,
    run_command,
    word_changes,
)
from core.voice.intake import VoiceIntake
from tests.integration.test_voice import SETTINGS, Echo, FakeModel, fake_download, make, voice_msg


def taught(*pairs: tuple[str, str]) -> SpeechDictionary:
    d = SpeechDictionary()
    for h, m in pairs:
        assert d.teach(h, m)
    return d


def test_fix_keeps_bangla_endings_and_whole_words() -> None:
    d = taught(("পিছি", "পিসি"), ("ওস্তা", "অবস্থা"))
    out = d.apply("পিছির ওস্তা বলো")
    assert out.text == "পিসির অবস্থা বলো"
    assert out.changes == (("পিছি", "পিসি"), ("ওস্তা", "অবস্থা"))
    # inside another word it is a different word — left alone
    assert d.apply("কাপিছিল").text == "কাপিছিল"


def test_fix_is_one_pass_and_case_free() -> None:
    d = taught(("skin shot", "screenshot"), ("screenshot", "photo"))
    assert d.apply("Skin  Shot dao").text == "screenshot dao"      # never fed into the next
    assert d.apply("ekta screenshots").text == "ekta screenshots"   # not an English ending


def test_both_nukta_spellings_match() -> None:
    d = taught(("নোভায়", "নোভা"))
    precomposed, split = "নোভায়", "নোভায়"
    assert d.apply(f"{precomposed} বলো").text == "নোভা বলো"
    assert d.apply(f"{split} বলো").text == "নোভা বলো"


def test_unsafe_or_pointless_teaching_refused() -> None:
    d = SpeechDictionary()
    assert not d.teach("token", "sk-ant-" + "b" * 30)
    assert not d.teach("এক দুই তিন চার পাঁচ", "x y")                   # too long to be a mishearing
    assert not d.teach("পিসি", "পিসি")
    assert not d.teach("x", "এক্স")                                    # too short to match safely
    assert d.fixes() == {}


def test_confirmed_twice_is_learned_once_is_not() -> None:
    d = SpeechDictionary()
    assert d.observe("পিছি বর্তমন ওস্তা বলো", "পিসির বর্তমান অবস্থা বলো") == []
    assert d.apply("ওস্তা বলো").text == "ওস্তা বলো"                     # one tap: not yet
    learned = d.observe("আমার পিছি বর্তমন ওস্তা দেখাও", "আমার পিসির বর্তমান অবস্থা দেখাও")
    assert learned == [("পিছি", "পিসি"), ("বর্তমন", "বর্তমান"), ("ওস্তা", "অবস্থা")]
    assert d.apply("পিছির ওস্তা বলো").text == "পিসির অবস্থা বলো"    # never "পিসিরর"


def test_a_rewritten_sentence_teaches_nothing() -> None:
    assert word_changes("পিসি খোলো", "আজকের আবহাওয়া কেমন জানাও প্লিজ") == []
    assert word_changes("চালাও notepad", "notepad চালাও") == []        # moved, not misheard
    assert word_changes("পিছি খোলো", "পিসি খোলো") == [("পিছি", "পিসি")]
    assert word_changes("পিছির খবর", "পিসির খবর") == [("পিছি", "পিসি")]   # the word, not its ending
    assert word_changes("skin shot nao", "screenshot nao") == [("skin shot", "screenshot")]


def test_two_different_corrections_do_not_add_up() -> None:
    d = SpeechDictionary()
    d.observe("ওস্তা বলো", "অবস্থা বলো")
    d.observe("ওস্তা বলো", "আস্থা বলো")                                  # a different meaning
    assert d.fixes() == {}


@pytest.mark.parametrize(("text", "want"), [
    ("শব্দ শেখো: পিছি মানে পিসি", ("teach", {"heard": "পিছি", "meant": "পিসি"})),
    ("shobdo shekho nobha mane Nova", ("teach", {"heard": "nobha", "meant": "Nova"})),
    ("learn word skin shot = screenshot", ("teach", {"heard": "skin shot",
                                                     "meant": "screenshot"})),
    ("শব্দ শেখো: রুমানব্যাংক", ("word", {"word": "রুমানব্যাংক"})),
    ("অভিধান দেখাও", ("list", {})),
    ("dictionary dekhao", ("list", {})),
    ("অভিধান থেকে পিছি মুছো", ("forget", {"phrase": "পিছি"})),
    ("/words forget পিছি", ("forget", {"phrase": "পিছি"})),
    ("পিসি মানে কী?", None),                     # a question, not a teaching
    ("এটা মনে রাখো", None),
])
def test_owner_commands(text: str, want: tuple[str, dict[str, str]] | None) -> None:
    assert dictionary_command(text) == want


def test_forget_by_either_side() -> None:
    d = taught(("পিছি", "পিসি"))
    assert "মুছে" in (run_command(d, "অভিধান থেকে পিসি মুছো") or "")
    assert d.fixes() == {}
    assert "নেই" in (run_command(d, "অভিধান থেকে পিসি মুছো") or "")


def test_words_feed_whisper_hints_on_change() -> None:
    d = SpeechDictionary()
    seen: list[list[str]] = []
    d.on_change.append(lambda: seen.append(d.words()))
    run_command(d, "শব্দ শেখো: রুমানব্যাংক")
    run_command(d, "শব্দ শেখো: পিছি মানে পিসি")
    assert seen[-1] == ["রুমানব্যাংক", "পিসি"]


def test_kept_in_the_database(tmp_path: Path) -> None:
    from tests.mocks.skills import make_skill_env
    sessions = make_skill_env(tmp_path).tasks.store._sessions
    d = SpeechDictionary(DbStore(sessions))
    assert d.teach("পিছি", "পিসি") and d.add_word("রুমানব্যাংক")
    d.observe("ওস্তা বলো", "অবস্থা বলো")
    again = SpeechDictionary(DbStore(sessions))                      # after a restart
    assert again.fixes() == {"পিছি": "পিসি"}
    assert again.words() == ["রুমানব্যাংক", "পিসি"]
    again.observe("ওস্তা দেখাও", "অবস্থা দেখাও")                          # the second tap
    assert SpeechDictionary(DbStore(sessions)).apply("ওস্তা").text == "অবস্থা"
    assert again.forget("পিছি") and SpeechDictionary(DbStore(sessions)).fixes() == {
        "ওস্তা": "অবস্থা"}


def test_voice_transcript_fixed_before_routing(tmp_path: Path) -> None:
    t, _ = make(tmp_path, FakeModel(("পিছির ওস্তা বলো", "bn", -0.05)))
    echo = Echo()
    vi = VoiceIntake(t, SETTINGS, tmp_path / "audio", echo,
                     dictionary=taught(("পিছি", "পিসি"), ("ওস্তা", "অবস্থা")))
    out = asyncio.run(vi.handle(voice_msg(), fake_download))
    assert echo.got == ["পিসির অবস্থা বলো"]
    assert "«পিছির ওস্তা বলো»" in out.text                         # what was heard stays visible
    assert "📖 অভিধানে ঠিক করলাম: «পিসির অবস্থা বলো»" in out.text


def test_confirm_button_counts_toward_learning(tmp_path: Path) -> None:
    t, _ = make(tmp_path, FakeModel(("ওস্তা বলো", "bn", -0.4)))
    d = SpeechDictionary()

    async def corrector(heard: str, lang: str) -> str:
        return "অবস্থা বলো"
    vi = VoiceIntake(t, SETTINGS, tmp_path / "audio", Echo(), corrector, dictionary=d)
    for _ in range(2):
        out = asyncio.run(vi.handle(voice_msg(), fake_download))
        yes = out.buttons[0][0][1]
        asyncio.run(vi.on_button(IncomingCallback("telegram", "555", "555", "c", yes, "1", "x")))
    assert d.fixes() == {"ওস্তা": "অবস্থা"}
    # "what you heard" is the owner saying the reading was right: nothing to learn
    d2 = SpeechDictionary()
    vi2 = VoiceIntake(t, SETTINGS, tmp_path / "audio", Echo(), corrector, dictionary=d2)
    for _ in range(2):
        out = asyncio.run(vi2.handle(voice_msg(), fake_download))
        heard = out.buttons[1][0][1]
        asyncio.run(vi2.on_button(IncomingCallback("telegram", "555", "555", "c", heard, "1",
                                                   "x")))
    assert d2.fixes() == {}


def _text(text: str) -> IncomingMessage:
    return IncomingMessage("telegram", "555", "555", "1", text, dt.datetime.now(dt.UTC))


def test_commands_answer_directly_never_a_task(tmp_path: Path) -> None:
    d = SpeechDictionary()
    router = CommandRouter(tmp_path, HealthSources(), "Nova", dictionary=d)
    out = asyncio.run(router.handle(_text("শব্দ শেখো: পিছি মানে পিসি")))
    assert out is not None and out.text.startswith("📖 শিখলাম")
    listing = asyncio.run(router.handle(_text("/words")))
    assert listing is not None and "«পিছি» → «পিসি»" in listing.text
    out = asyncio.run(router.handle(_text("/words skin shot = screenshot")))
    assert out is not None and d.fixes()["skin shot"] == "screenshot"
    assert "/words" in [c for c, _ in router.menu()]


def test_teaching_with_nothing_heard_is_refused() -> None:
    d = SpeechDictionary()
    assert dictionary_command("শব্দ শেখো:  মানে তানজিলা") == (
        "teach", {"heard": "", "meant": "তানজিলা"})
    assert (run_command(d, "শব্দ শেখো:  মানে তানজিলা") or "").startswith("❌")
    assert d.words() == []


def test_owner_words_go_first_in_whisper_hotwords(tmp_path: Path) -> None:
    from core.voice.transcriber import Transcriber
    model = FakeModel(("রুমানব্যাংক খোলো", "bn", -0.05))
    (tmp_path / "model").mkdir()
    t = Transcriber(tmp_path / "model", factory=lambda *a: model, hint_words=["পিসি"])
    t.hotwords = "Chrome, Notepad"                      # installed app names (V1.1)
    t.owner_words = "রুমানব্যাংক, তানজিলা"
    asyncio.run(t.transcribe(tmp_path / "model"))
    call = model.calls[0]
    assert call["hotwords"] == "রুমানব্যাংক, তানজিলা, Chrome, Notepad"
    assert call["initial_prompt"] == "পিসি"              # the prompt is left as it was
