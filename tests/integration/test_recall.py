"""V1.3-2 searching old conversations through the real task engine and the real DB
(FTS5 index from migration 006): the right earlier talk is found and summed up by this
PC's AI only, from those records only; time words narrow it; nothing found is said
plainly; no AI → the list alone; a read-back or an ordinary question is not a search."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from core.db.models import Task
from core.orchestrator.executor import UserRequestExecutor
from core.orchestrator.recall import ConversationSearch, recall_request
from core.queue.states import TaskState
from core.router.intent import IntentRouter
from models.router import ProviderRouter
from providers.provider_base import ErrorCategory
from tests.integration.test_specialist_agents import run_all, submit
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import SkillEnv, make_skill_env

TZ = "Asia/Dhaka"


def setup(tmp_path: Path, local: list) -> tuple[SkillEnv, FakeAdapter]:  # type: ignore[type-arg]
    from core.config import load_config
    from providers.provider_base import HealthState
    s = make_skill_env(tmp_path)
    cfg = load_config()
    adapters = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in cfg.providers.providers}
    adapters["ollama_local"] = ai = FakeAdapter("ollama_local", local)
    router = ProviderRouter(cfg.providers, adapters)
    ex = UserRequestExecutor(IntentRouter(router), router, s.runner)
    ex.history = ConversationSearch(s.tasks.store._sessions, TZ)
    s.tasks.engine.register("user_request", ex)
    return s, ai


def past(s: SkillEnv, request: str, answer: str, days_ago: float) -> int:
    """A finished conversation from `days_ago` days back."""
    tid = int(s.tasks.store.create(title=request, request_text=request,
                                   task_type="user_request", channel="telegram",
                                   chat_id="555").id)
    when = datetime.now(UTC) - timedelta(days=days_ago)
    with s.tasks.store._sessions() as db:
        db.execute(update(Task).where(Task.id == tid).values(
            result_summary=answer, state=TaskState.COMPLETED, created_at=when,
            finished_at=when))
        db.commit()
    return tid


def ask(s: SkillEnv, text: str) -> tuple[str, str]:
    tid = submit(s, text)
    run_all(s)
    t = s.tasks.store.get(tid)
    assert t is not None and t.state is TaskState.COMPLETED, t and t.error_message
    return str(t.result_summary), [m for _, m in s.tasks.sent][-1]


def seed(s: SkillEnv) -> dict[str, int]:
    return {
        "pc": past(s, "পিসির অবস্থা বলো", "CPU 15%, RAM 73%, D: ড্রাইভে 82 GB খালি", 3),
        "capital": past(s, "বাংলাদেশের রাজধানী কী?", "বাংলাদেশের রাজধানী ঢাকা।", 10),
        "notepad": past(s, "notepad খোলো", "notepad খোলা হয়েছে ✅", 0.01),
    }


def test_the_right_earlier_talk_is_found_and_summed_up_by_this_pcs_ai(
        tmp_path: Path) -> None:
    s, ai = setup(tmp_path, ["৩ দিন আগে আপনি পিসির অবস্থা জানতে চেয়েছিলেন: CPU 15%।"])
    ids = seed(s)
    answer, sent = ask(s, "গত সপ্তাহে পিসির স্ট্যাটাস নিয়ে কী বলেছিলাম?")
    assert answer.startswith("৩ দিন আগে আপনি পিসির অবস্থা")
    assert f"(task #{ids['pc']}): «পিসির অবস্থা বলো»" in answer          # what it rests on
    assert "রাজধানী" not in answer and "notepad" not in answer
    (req,) = ai.requests                                   # one AI call: the summary
    assert req.privacy == "local_only" and req.task_type == "quick_answer"
    assert "CPU 15%" in req.context and "ঢাকা" not in req.context     # only what was found
    assert "আগের কথা থেকে" in sent                          # the verifier's proof line


def test_time_words_narrow_the_search(tmp_path: Path) -> None:
    s, _ai = setup(tmp_path, ["x"])
    ids = seed(s)
    answer, _ = ask(s, "রাজধানী নিয়ে গতকাল কী বলেছিলাম?")       # it was 10 days ago
    assert "খুঁজে পাইনি" in answer and "গতকাল" in answer
    answer, _ = ask(s, "গত মাসে রাজধানী নিয়ে কী বলেছিলাম?")
    assert f"task #{ids['capital']}" in answer


def test_nothing_found_is_said_plainly_and_no_ai_is_asked(tmp_path: Path) -> None:
    s, ai = setup(tmp_path, [])
    seed(s)
    answer, sent = ask(s, "ক্রিকেট নিয়ে কী কথা হয়েছিল?")
    assert answer == "🔎 «ক্রিকেট» নিয়ে আগের কোনো কথা খুঁজে পাইনি।"
    assert ai.requests == []                     # neither a classifier nor a summary
    assert "কিছু পাওয়া যায়নি" in sent


def test_without_the_local_ai_the_list_alone_answers(tmp_path: Path) -> None:
    s, _ai = setup(tmp_path, [ErrorCategory.MODEL_UNAVAILABLE] * 3)
    ids = seed(s)
    answer, _ = ask(s, "what did I ask about notepad")
    assert answer.startswith("🔎 Found:") and f"task #{ids['notepad']}" in answer


def test_the_index_follows_every_change_to_a_task(tmp_path: Path) -> None:
    s, _ = setup(tmp_path, [])
    tid = past(s, "ব্যাকআপ কোথায় রাখব?", "D: ড্রাইভে রাখুন", 1)
    search = ConversationSearch(s.tasks.store._sessions, TZ)
    q = recall_request("ব্যাকআপ নিয়ে কী বলেছিলে?")
    assert q is not None
    assert [h.task_id for h in search.search("555", q, 10**6)] == [tid]
    q2 = recall_request("এক্সটার্নাল নিয়ে কী বলেছিলে?")
    assert q2 is not None and search.search("555", q2, 10**6) == []
    with s.tasks.store._sessions() as db:          # the answer changes → the index too
        db.execute(update(Task).where(Task.id == tid).values(
            result_summary="এক্সটার্নাল হার্ডডিস্কে রাখুন"))
        db.commit()
    assert [h.task_id for h in search.search("555", q2, 10**6)] == [tid]
    assert search.search("other-chat", q2, 10**6) == []      # never another chat's talk


@pytest.mark.parametrize("text", [
    "গত সপ্তাহে পিসির স্ট্যাটাস নিয়ে কী বলেছিলাম?", "Notepad নিয়ে কী কথা হয়েছিল",
    "what did I ask about the backup last week", "গতকাল কী কী জিজ্ঞেস করেছিলাম",
    "৩ দিন আগে বাংলাদেশের রাজধানী নিয়ে কী বলেছিলে", "amar ager kothopokothon dekhao",
    "pc ki bolechilam kal"])
def test_questions_about_earlier_talks_are_recognised(text: str) -> None:
    assert recall_request(text) is not None


@pytest.mark.parametrize("text", [
    # a read-back (owner 2026-10-26, a real recording) — not a search
    "আমি তোমাকে বলেছিলাম, পিসির যে লাস্ট স্ট্যাটাস আমাকে দিলে, সেটি আমাকে পুরোটা পড়ে শোনাও",
    "পিসির অবস্থা বলো", "বাংলাদেশের রাজধানী কী?", "আমি বুঝলাম না, নোভা কেন কাজ করে না",
    "what is the capital of Bangladesh", "notepad খোলো"])
def test_other_requests_are_not_a_search(text: str) -> None:
    assert recall_request(text) is None


def test_time_and_topic_are_read_from_the_words() -> None:
    q = recall_request("গত সপ্তাহে পিসির স্ট্যাটাস নিয়ে কী বলেছিলাম?")
    assert q is not None and q.terms == ("পিসি", "স্ট্যাটাস") and q.days == (14, -1)
    q = recall_request("pc ki bolechilam kal")
    assert q is not None and q.terms == ("pc",) and q.when == "গতকাল"
    q = recall_request("amar ager kothopokothon dekhao")
    assert q is not None and q.terms == () and q.days == (7, -1)


def test_yesterday_is_only_yesterday() -> None:
    """Real run 2026-10-26: «গতকাল কী কী জিজ্ঞেস করেছিলাম» listed today's talks too."""
    search = ConversationSearch(None, TZ)  # type: ignore[arg-type]
    now = datetime(2026, 10, 26, 11, 40, tzinfo=UTC)               # 17:40 in Dhaka
    since, until = search._range((1, 0), now)
    assert (since, until) == ("2026-10-24 18:00:00", "2026-10-25 18:00:00")   # 25 Oct
    since, until = search._range((0, -1), now)                      # today: up to now
    assert since == "2026-10-25 18:00:00" and until >= "2026-10-26 11:40:00"
