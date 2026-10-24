"""V1.1 app name resolver: misheard / Bangla-script / learned names → installed apps.
The transcripts are real Whisper outputs from the owner's phone (2026-10-16) and the
voice benchmark (scripts/voice_corpus.json)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.apps.catalog import Catalog, Found, build
from core.apps.phonetic import key, normalize, romanize, similarity, spelled
from core.apps.resolver import AppResolver, MemoryAliases, safe_alias
from core.router.intent import IntentRouter, classify_by_rules

FOUND = [
    Found("Adobe Premiere Pro 2026", "lnk", r"C:\sm\Adobe Premiere Pro 2026.lnk",
          "Adobe Premiere Pro.exe"),
    Found("Adobe After Effects 2025", "lnk", r"C:\sm\Adobe After Effects 2025.lnk",
          "AfterFX.exe"),
    Found("Adobe Photoshop 2025", "lnk", r"C:\sm\ps.lnk", "Photoshop.exe"),
    Found("Adobe Illustrator 2025", "lnk", r"C:\sm\ai.lnk", "Illustrator.exe"),
    Found("Adobe Audition 2026", "lnk", r"C:\sm\au.lnk", "Adobe Audition.exe"),
    Found("CapCut", "lnk", r"C:\sm\CapCut.lnk", "CapCut.exe"),
    Found("OBS Studio", "lnk", r"C:\sm\OBS Studio.lnk", "obs64.exe"),
    Found("Audacity", "lnk", r"C:\sm\Audacity.lnk", "Audacity.exe"),
    Found("VLC media player", "lnk", r"C:\sm\vlc.lnk", "vlc.exe"),
    Found("Visual Studio Code", "lnk", r"C:\sm\code.lnk", "Code.exe"),
    Found("Word", "lnk", r"C:\sm\Word.lnk", "WINWORD.EXE"),
    Found("Excel", "lnk", r"C:\sm\Excel.lnk", "EXCEL.EXE"),
    Found("Steam", "lnk", r"C:\sm\Steam.lnk", "Steam.exe"),
    Found("Task Manager", "lnk", r"C:\sm\tm.lnk", "Taskmgr.exe"),
    Found("Run", "lnk", r"C:\sm\Run.lnk", ""),
    Found("Git GUI", "lnk", r"C:\sm\gitgui.lnk", "git-gui.exe"),
    Found("KMPlayer 64X", "lnk", r"C:\sm\km.lnk", "KMPlayer64.exe"),
    Found("Calculator", "uwp", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", ""),
    Found("Clock", "uwp", "Microsoft.WindowsAlarms_8wekyb3d8bbwe!App", ""),
    Found("Photos", "uwp", "Microsoft.Windows.Photos_8wekyb3d8bbwe!App", ""),
]


def resolver(tmp_path: Path, now: float = 1000.0) -> AppResolver:
    cat = build(FOUND, now=now)
    path = tmp_path / "app_catalog.json"
    cat.save(path)

    def no_scan() -> Catalog:
        raise AssertionError("must not rescan")
    return AppResolver(path, MemoryAliases(), scanner=no_scan, clock=lambda: now + 1)


# ------------------------------------------------------------------ phonetic


@pytest.mark.parametrize(("heard", "name"), [
    ("বিএস কোট", spelled("vs") + " code"), ("বিয়ে স্কুট", spelled("vs") + " code"),
    ("ক্যাপকাট", "capcut"), ("কেপকাত", "capcut"), ("আফটার ইফেক্টস", "after effects"),
    ("ও বি এস", spelled("obs")), ("কালকুলেটোর", "calculator"), ("এক্সেল", "excel"),
    ("BS Code", "vs code"),
])
def test_sound_keys_match(heard: str, name: str) -> None:
    assert similarity(key(heard), key(name)) >= 0.86, (romanize(heard), key(heard), key(name))


def test_normalize_keeps_bangla_vowel_signs() -> None:
    # combining marks are not \w — they must not split words ("প্রিমিযার" ≠ "প র ম")
    assert normalize("প্রিমিযার প্রখোলো!") == "প্রিমিযার প্রখোলো"


# ------------------------------------------------------------------ catalog


def test_catalog_ties_spoken_names_and_marks_missing_apps(tmp_path: Path) -> None:
    cat = build(FOUND, now=1.0)
    pr = cat.get("premiere-pro")
    assert pr is not None and pr.kind == "lnk" and pr.process == ("Adobe Premiere Pro.exe",)
    assert "প্রিমিয়ার প্রো" in pr.aliases and "Premiere Pro" in pr.aliases
    vs = cat.get("vscode")                    # the fixed app-control app, not listed twice
    assert vs is not None and vs.kind == "builtin" and "visual-studio-code" not in cat.apps
    missing = cat.get("visual-studio")
    assert missing is not None and not missing.installed
    word = cat.get("word")
    assert word is not None and word.process == ("WINWORD.EXE",)
    cat.save(tmp_path / "c.json")
    again = Catalog.load(tmp_path / "c.json")
    assert again is not None and again.apps == cat.apps


# ------------------------------------------------------------------ resolution


@pytest.mark.parametrize(("text", "action", "app"), [
    ("আমার পিসিতে Premiere Pro ওপেন করো", "open", "premiere-pro"),
    ("প্রিমিযার প্রখোলো", "open", "premiere-pro"),                 # verb glued on
    ("আমার পিসিতে প্রিমিয় প্রো পেন করো", "open", "premiere-pro"),     # "ও" dropped
    ("After Effects চালো করো", "open", "after-effects"),
    ("After Effects Open Coro", "open", "after-effects"),
    ("তুমি জাস্ট BS Code অপেন করো", "open", "vscode"),
    ("আমি বলে ছি, আমার পিসি তে বিয়ে স্কুট এপস্ট ওপেন করো", "open", "vscode"),
    ("তুমি জাস্ট বিএস কোট অপেন করো, তার পর যামি তুমাকে কমান কো দিয়ো", "open", "vscode"),
    ("amer pc ta vs code open koro", "open", "vscode"),
    ("কেপকাত খুলো", "open", "capcut"),
    ("আবস ওপিন করো", "open", "obs"),
    ("আবস্ বন্ধ করো", "close", "obs"),
    ("Audacity খোলো", "open", "audacity"),
    ("হটোশ্প খোলো", "open", "photoshop"),
    ("স্টিম উপেন করো", "open", "steam"),
    ("কালকুলেটোর খোলো", "open", "calculator"),
    ("টাস্ক মেনেজার ওপেন করো", "open", "task-manager"),
    ("Open Microsoft Word", "open", "word"),
    ("Chrome Kolo", "open", "chrome"),
])
def test_misheard_names_resolve(tmp_path: Path, text: str, action: str, app: str) -> None:
    hit = classify_by_rules(text, resolver(tmp_path), "c")
    assert hit is not None and hit.skill == ("app-control", action, {"app": app}), hit


def test_not_installed_app_is_said_honestly_with_the_closest(tmp_path: Path) -> None:
    res = resolver(tmp_path).resolve_command("Open Visual Studio", "c")
    assert res is not None and res.status == "missing" and res.best is not None
    assert res.best.app.name == "Visual Studio"
    assert res.candidates[0].app.id == "vscode"


def test_unclear_name_asks_and_the_answer_is_learned(tmp_path: Path) -> None:
    r = resolver(tmp_path)
    ir = IntentRouter(None, apps=r)
    first = asyncio.run(ir.classify("ইলাস্টেটো চালু করো", 1, "c"))
    ask = first.meta["app_ask"]
    assert first.skill is None and ask["status"] == "ask"
    assert first.meta["app_choices"][0] == "illustrator"
    picked = asyncio.run(ir.classify("হ্যাঁ", 2, "c"))
    assert picked.skill == ("app-control", "open", {"app": "illustrator"})
    assert picked.meta["app_learned"] is True
    # next time the same misheard name is known — no question
    again = asyncio.run(ir.classify("ইলাস্টেটো চালু করো", 3, "c"))
    assert again.skill == ("app-control", "open", {"app": "illustrator"})


def test_answer_by_number_or_name_and_other_chats_untouched(tmp_path: Path) -> None:
    r = resolver(tmp_path)
    ir = IntentRouter(None, apps=r)
    asyncio.run(ir.classify("কেপকার উপেন করো", 1, "c"))
    assert asyncio.run(ir.classify("1", 2, "other")).skill is None    # another chat
    hit = asyncio.run(ir.classify("২", 3, "c"))
    assert hit.skill is not None and hit.skill[1] == "open"
    # an unrelated command right after a question is not taken as the answer
    asyncio.run(ir.classify("ক্রম কোলো", 4, "c"))
    hit = asyncio.run(ir.classify("পিসির অবস্থা কি", 5, "c"))
    assert hit.skill == ("windows", "status", {})


def test_correction_teaches_the_misheard_name(tmp_path: Path) -> None:
    r = resolver(tmp_path)
    ir = IntentRouter(None, apps=r)
    asyncio.run(ir.classify("টিমো খোলো", 1, "c"))           # unclear / wrong
    fixed = asyncio.run(ir.classify("আমি Premiere Pro বলেছি", 2, "c"))
    assert fixed.skill == ("app-control", "open", {"app": "premiere-pro"})
    assert r.learned.all() == {"টিমো": "premiere-pro"}
    assert asyncio.run(ir.classify("টিমো খোলো", 3, "c")).skill == (
        "app-control", "open", {"app": "premiere-pro"})
    # Banglish and English corrections too
    asyncio.run(ir.classify("kepkar open koro", 4, "d"))
    assert asyncio.run(ir.classify("ami capcut bolechi", 5, "d")).skill == (
        "app-control", "open", {"app": "capcut"})
    # a WRONG app (not just an unclear one) is corrected too
    wrong = asyncio.run(ir.classify("open audition", 6, "e"))
    assert wrong.skill == ("app-control", "open", {"app": "audition"})
    assert asyncio.run(ir.classify("I said Audacity", 7, "e")).skill == (
        "app-control", "open", {"app": "audacity"})


def test_correction_without_a_recent_app_request_is_ignored(tmp_path: Path) -> None:
    r = resolver(tmp_path)
    assert r.correction("c", "আমি Premiere Pro বলেছি") is None


@pytest.mark.parametrize("text", [
    "Untitled নামে একটা ফোল্ডার আছে, সেটি অপন করা",        # a folder, not an app
    "D:\\work\\report.pdf open koro",
    "prothomalo.com kholo",
    "PC restart koro",
    "print spooler restart koro",
    "বাংলাদেশের রাজধানী কী",
])
def test_not_app_commands(tmp_path: Path, text: str) -> None:
    hit = classify_by_rules(text, resolver(tmp_path), "c")
    assert hit is None or not (hit.skill and hit.skill[0] == "app-control"), hit
    assert hit is None or not hit.meta.get("app_ask")


def test_safe_alias_never_learns_secrets_or_numbers() -> None:
    assert safe_alias("প্রিমোর")
    assert not safe_alias("sk-" + "x" * 32)
    assert not safe_alias("password 123456")
    assert not safe_alias("open koro")                     # only command words
    assert not safe_alias("a" * 60)


def test_unknown_name_rescans_once_for_a_new_install(tmp_path: Path) -> None:
    cat = build(FOUND, now=0.0)
    path = tmp_path / "c.json"
    cat.save(path)
    scans: list[int] = []

    def scanner() -> Catalog:
        scans.append(1)
        return build([*FOUND, Found("Telegram Desktop", "lnk", r"C:\sm\tg.lnk",
                                    "Telegram.exe")], now=10_000.0)
    r = AppResolver(path, MemoryAliases(), scanner=scanner, clock=lambda: 10_000.0)
    res = r.resolve_command("telegram open koro", "c")
    assert scans == [1] and res is not None and res.app is not None
    assert res.app.id == "telegram"


def test_db_aliases_survive(tmp_path: Path) -> None:
    from core.apps.service import DbAliases
    from tests.mocks.skills import make_skill_env
    s = make_skill_env(tmp_path)
    store = DbAliases(s.tasks.store._sessions)
    assert store.learn("প্রিমোর", "premiere-pro", "owner correction")
    assert not store.learn("token sk-" + "x" * 32, "x", "owner")
    assert store.learn("প্রিমোর", "after-effects", "owner correction")   # latest wins
    assert DbAliases(s.tasks.store._sessions).all() == {"প্রিমোর": "after-effects"}


@pytest.mark.parametrize(("text", "want"), [
    ("volume 30 koro", ("device", "volume", {"level": 30})),
    ("ভলিউম ৪০ করো", ("device", "volume", {"level": 40})),
    ("আওয়াজ কমাও", ("device", "volume", {"change": -10})),
    ("sound 20 kom koro", ("device", "volume", {"change": -20})),
    ("mute koro", ("device", "volume", {"mute": True})),
    ("সাউন্ড অফ করো", ("device", "volume", {"mute": True})),
    ("আনমিউট করো", ("device", "volume", {"mute": False})),
    ("volume koto?", ("device", "volume", {})),
    ("পরের গান চালাও", ("device", "media", {"action": "next"})),
    ("gaan thamao", ("device", "media", {"action": "play_pause"})),
    ("brightness 50", ("device", "brightness", {"level": 50})),
    ("video trim koro", None), ("pause koro", None), ("Premiere Pro খোলো", None),
])
def test_device_words_need_no_ai(text: str, want: object) -> None:
    """V1.2: volume / media / brightness from fixed rules — and the fast lane."""
    from core.router.device import device_command
    from core.router.intent import QUICK_SKILLS, classify_by_rules
    assert device_command(text) == want
    if want is not None:
        hit = classify_by_rules(text)
        assert hit is not None and hit.skill == want and hit.skill[:2] in QUICK_SKILLS
