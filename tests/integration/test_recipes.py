"""V1.2 learned replay (Atticus item 4) through the real task engine: an operator run
the verifier PASSES is kept; the same request — said a little differently — runs again
with no AI at all; a recipe that stops working is dropped and the AI does the work."""

from __future__ import annotations

import time
from pathlib import Path

from core.orchestrator.executor import UserRequestExecutor
from core.orchestrator.recipes import RecipeBook, gist, same_request
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from core.voice.dictionary import MemoryStore
from models.router import ProviderRouter
from tests.integration.test_specialist_agents import answer, call, run_all, submit
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import SkillEnv, make_skill_env


def setup(tmp_path: Path, plans: list[str]) -> tuple[SkillEnv, FakeAdapter, RecipeBook,
                                                      Path]:
    from core.config import load_config
    from providers.provider_base import HealthState
    s = make_skill_env(tmp_path)
    cfg = load_config()
    adapters = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in cfg.providers.providers}
    local = adapters["ollama_local"] = FakeAdapter("ollama_local", plans)
    router = ProviderRouter(cfg.providers, adapters)
    ex = UserRequestExecutor(IntentRouter(router), router, s.runner)
    book = ex.recipes = RecipeBook(MemoryStore())
    s.tasks.engine.register("user_request", ex)
    ws = s.runner.env.config.path("workspace_dir")
    ws.mkdir(parents=True, exist_ok=True)
    return s, local, book, ws


def _done(s: SkillEnv, text: str) -> tuple[str, str]:
    tid = submit(s, text)
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t is not None
    assert t.state is TaskState.COMPLETED, t.error_message
    return str(t.result_summary), [txt for _, txt in s.tasks.sent][-1]


def test_a_verified_run_is_done_again_with_no_ai(tmp_path: Path) -> None:
    s, local, book, ws = setup(tmp_path, [])
    folder = ws / "reports"
    local.script.extend([call("files", "mkdir", path=str(folder)),
                            answer("reports ফোল্ডার বানানো হয়েছে।")])
    text = f"{ws} এর ভিতরে reports নামে ফোল্ডার বানাও"
    first, _ = _done(s, text)
    assert folder.is_dir() and "⚡" not in first
    asked = len(local.requests)
    assert asked >= 2                                    # the AI planned it the first time
    folder.rmdir()
    # the same request, said a little differently — no AI answer is left to give
    again, sent = _done(s, f"নোভা {ws} এর ভিতরে reports নামে ফোল্ডার বানাও প্লিজ")
    assert folder.is_dir()                               # really done again
    assert len(local.requests) == asked                  # not one AI request
    assert again.startswith("reports ফোল্ডার বানানো হয়েছে।") and "⚡" in again
    assert "reports আছে ✓" in sent                        # the verifier checked the PC


def test_a_recipe_that_stops_working_is_dropped_and_the_ai_does_it(tmp_path: Path) -> None:
    s, local, book, ws = setup(tmp_path, [])
    src, dst = ws / "a.txt", ws / "b.txt"
    src.write_text("x", encoding="utf-8")
    local.script.extend([call("files", "copy", source=str(src), destination=str(dst)),
                            answer("a.txt কপি করে b.txt বানানো হয়েছে।")])
    text = f"{ws} ফোল্ডারে a.txt ফাইল b.txt নামে কপি করো"
    _done(s, text)
    assert book.find(text) is not None
    dst.unlink()
    src.unlink()                                         # the recipe cannot work now
    other = ws / "a2.txt"
    other.write_text("y", encoding="utf-8")
    local.script.extend([call("files", "copy", source=str(other), destination=str(dst)),
                            answer("a2.txt থেকে b.txt বানানো হয়েছে।")])
    result, _ = _done(s, text)
    assert dst.read_text(encoding="utf-8") == "y" and "⚡" not in result   # the AI did it
    assert book.find(text) is not None                   # …and the new way is kept
    assert book.find(text).calls[0]["params"]["source"] == str(other)  # type: ignore[union-attr]


# ------------------------------------------------------------------- what is kept

def _op(*effects: dict, answer_text: str = "হয়ে গেছে।") -> dict:  # type: ignore[type-arg]
    return {"kind": "operator", "ok": True, "answer": answer_text,
            "evidence": {"agent": "windows_operator", "effects": list(effects)}}


def _e(skill: str, tool: str, **params: object) -> dict:  # type: ignore[type-arg]
    return {"skill": skill, "tool": tool, "params": params, "evidence": {}}


def test_only_safe_lasting_actions_are_kept() -> None:
    book = RecipeBook(MemoryStore())
    assert book.learn("নোভা পিসি রিস্টার্ট করো", _op(_e("admin", "power", op="restart")))
    assert book.find("পিসি রিস্টাট করো প্লিজ") is not None        # heard a little differently
    assert book.find("পিসি শাটডাউন করো") is None                    # another request
    never = [
        ("x.txt মুছে দাও", _op(_e("files", "delete", path="x.txt"))),
        ("ok বাটনে চাপ দাও", _op(_e("desktop-ui", "click", window="w", name="OK"))),
        ("এটা বন্ধ করো", _op(_e("app-control", "close", app="notepad"))),       # "this"
        ("আজকের checklist ফাইল বানাও", _op(_e("files", "write", path="c.txt",
                                               content="আজ: ১. বাজার"))),       # "today"
        ("notes.txt এ একটা কবিতা লেখো", _op(_e("files", "write", path="notes.txt",
                                              content="the AI's own poem, long enough"))),
        ("চারটা app খোলো", _op(*[_e("app-control", "open", app=a)
                                 for a in ("a", "b", "c", "d")])),             # too many
        ("পাসওয়ার্ড hunter2 টাইপ করো", _op(_e("app-control", "open", app="x"))),
    ]
    for text, act in never:
        assert not book.learn(text, act), text
    assert not book.learn("notepad খোলো", {"kind": "ai", "ok": True, "answer": "x"})
    # the owner's own words may be written again
    assert book.learn("notes.txt এ hello nova from the owner লেখো",
                      _op(_e("files", "write", path="notes.txt",
                             content="hello nova from the owner")))


def test_a_reply_with_numbers_is_not_repeated_and_unused_recipes_go() -> None:
    clock = [time.time()]
    book = RecipeBook(MemoryStore(), now=lambda: clock[0])
    book.learn("notepad খোলো", _op(_e("app-control", "open", app="notepad"),
                                     answer_text="notepad খুলেছি, pid 4242"))
    r = book.find("notepad খোলো")
    assert r is not None and r.answer == ""              # fresh results instead
    clock[0] += 31 * 86400
    assert book.find("notepad খোলো") is None             # 30 days unused: forgotten


def test_requests_match_by_sound_but_never_across_meanings() -> None:
    same = [("নোভা পিসি রিস্টার্ট করো", "পিসি রিস্টাট করো প্লিজ"),
            ("pc off koro", "nova pc off koro plz"),
            ("নোভা পিসিস হাটডান করো", "পিসি শাটডাউন করো")]
    different = [("pc off koro", "pc on koro"), ("volume 30 koro", "volume 40 koro"),
                 ("notepad খোলো", "notepad বন্ধ করো"), ("notepad খোলো", "wordpad খোলো"),
                 ("পিসি রিস্টার্ট করো", "পিসি শাটডাউন করো"),
                 ("downloads ফোল্ডার খোলো", "documents ফোল্ডার খোলো"),
                 ("firewall চালু করো", "firewall বন্ধ করো")]
    for a, b in same:
        assert same_request(gist(a), gist(b)), (a, b)
    for a, b in different:
        assert not same_request(gist(a), gist(b)), (a, b)


def test_a_path_with_digits_keeps_the_owners_language() -> None:
    """Real run 2026-10-26: the Bangla reply named a folder «nova_recipe_fqfd0g9h» — its
    digits are no changing fact; the replay must not fall back to an English summary."""
    book = RecipeBook(MemoryStore())
    reply = r"D:\work\run_0g9h\reports নামে ফোল্ডার বানানো হয়েছে। report2.txt ঠিক আছে।"
    book.learn("reports নামে ফোল্ডার বানাও",
               _op(_e("files", "mkdir", path=r"D:\work\run_0g9h\reports"), answer_text=reply))
    r = book.find("reports নামে ফোল্ডার বানাও")
    assert r is not None and r.answer == reply


def test_a_garbled_reply_is_never_kept_to_be_said_again() -> None:
    """Real run 2026-10-26: the folder was made, but the reply was a made-up «আগের কোনো
    কাজের ফলাফল পাওয়া যায়নি…» — the recipe keeps the calls, not that reply."""
    book = RecipeBook(MemoryStore())
    book.learn("reports নামে ফোল্ডার বানাও",
               _op(_e("files", "mkdir", path=r"D:\work\reports"),
                   answer_text="আগের কোনো কাজের ফলাফল বা সম্পূর্ণ লেখা পাওয়া যায়নি।"))
    r = book.find("reports নামে ফোল্ডার বানাও")
    assert r is not None and r.answer == "" and r.calls[0]["tool"] == "mkdir"


def test_the_operator_never_accepts_a_reply_that_says_nothing(tmp_path: Path) -> None:
    """The same run: the local planner "answered" «D:\\» — it is asked again."""
    s, local, _book, ws = setup(tmp_path, [])
    folder = ws / "reports"
    local.script.extend([call("files", "mkdir", path=str(folder)), answer("D:\\"),
                         answer("reports ফোল্ডার বানানো হয়েছে।")])
    result, _ = _done(s, f"{ws} এর ভিতরে reports নামে ফোল্ডার বানাও")
    assert result == "reports ফোল্ডার বানানো হয়েছে।"
    assert "rejected: empty answer" in local.requests[-1].context
