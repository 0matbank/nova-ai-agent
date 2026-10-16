"""Phase 16 pass — "Correct role + provider + skill routing works": every request
gets the right specialist(s), project specialist, kind of AI work (→ provider)
and skill allowlist; never more than 3 agents, never without the verifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agents import Assignment, Roster, assign, find_project
from core.agents.roster import AGENTS_DIR
from core.config import load_config
from core.router.intent import Intent, classify_by_rules
from models.router import ProviderRouter
from providers.provider_base import HealthState
from tests.mocks.providers import FakeAdapter

CFG = load_config()
ROSTER = Roster.load(CFG)


def route(text: str) -> Assignment:
    intent = classify_by_rules(text) or Intent("question", 0.3, "default")
    return assign(intent, text, CFG, ROSTER)


# text → lead agent, route, project, provider task type
CASES = [
    ("pc status bolo", "windows_operator", "skill", None, None),
    ("notepad kholo", "windows_operator", "skill", None, None),
    ("screen e ki error dekhacche?", "windows_operator", "skill", None, "vision"),
    ("Downloads folder e koyta pdf file ache?", "file_document", "operator", None, "planning"),
    ("git demo te ekta bug fix koro", "developer", "coding", "git-demo", "coding"),
    ("click tv er button er color change koro", "frontend_ui", "coding", "click-tv", "coding"),
    ("android app er gradle code fix koro", "android", "coding", None, "coding"),
    ("git demo te api endpoint er code add koro", "backend_architect", "coding", "git-demo",
     "coding"),
    ("git demo er github actions workflow file update koro", "devops", "coding", "git-demo",
     "coding"),
    ("Git Demo test koro", "project:git-demo", "project_test", "git-demo", None),
    ("Click TV test koro", "project:click-tv", "project_test", "click-tv", None),
    ("git demo er git status dekhao", "git", "git", "git-demo", None),
    ("git demo te kono secret leak ache kina check koro", "security", "operator", "git-demo",
     "planning"),
    ("git demo er security bug ta fix koro", "developer", "coding", "git-demo", "coding"),
    ("Downloads e kono secret leak ache?", "security", "operator", None, "planning"),
    ("aajker news ki", "research", "answer", None, "current_info"),
    ("m3u8 playlist er code e bug fix koro", "developer", "coding", "stream-doctor", "coding"),
    ("prothomalo.com kholo", "browser", "browser", None, "planning"),
    ("kal sokal 8 tay remind koro", "automation", "not_yet", None, None),
    ("git demo project ta ki?", "master_orchestrator", "project_info", "git-demo",
     "quick_answer"),
    ("বাংলাদেশের রাজধানী কী?", "master_orchestrator", "answer", None, "quick_answer"),
    # Phase 17: a note for the project memory — no AI
    ("Click TV te mone rakho: player HLS.js use kore", "project:click-tv", "project_remember",
     "click-tv", None),
    ("ক্লিক টিভি-র জন্য মনে রাখো: স্ক্যানার প্রতি ঘণ্টায় চলে", "project:click-tv",
     "project_remember", "click-tv", None),
]


@pytest.mark.parametrize(("text", "lead", "route_", "project", "ptype"), CASES)
def test_request_goes_to_the_right_specialist(text: str, lead: str, route_: str,
                                               project: str | None, ptype: str | None) -> None:
    a = route(text)
    assert (a.lead, a.route, a.project, a.provider_task_type) == (lead, route_, project, ptype), \
        a.reason
    # §58/§61: few agents per request, the verifier always among them (§30)
    assert len(a.agents) <= CFG.agents.runtime.max_active_agents_per_task
    assert a.agents[-1] == "verifier" and a.agents[0] in (lead, f"project:{project}")
    if project:
        assert f"project:{project}" in a.agents          # project context first (§52.1)
    assert Assignment.from_json(a.to_json()) == a          # survives the task checkpoint


def test_each_agent_gets_only_its_own_skills() -> None:
    files = route("Downloads folder e koyta pdf file ache?")
    assert files.allows("files", "delete") and files.allows("search", "find")
    assert not files.allows("powershell", "run") and not files.allows("app-control", "open")
    pc = route("notepad kholo")
    assert pc.allows("app-control", "open") and pc.allows("files", "list_dir")
    assert not pc.allows("files", "delete") and not pc.allows("files", "write")
    sec = route("git demo te kono secret leak ache kina check koro")
    assert sec.allows("git", "diff") and sec.allows("files", "read_text")
    assert not sec.allows("git", "commit") and not sec.allows("git", "push")
    assert not sec.allows("files", "write")
    dev = route("git demo te ekta bug fix koro")
    assert dev.allows("git", "commit") and not dev.allows("powershell", "run")
    assert route("বাংলাদেশের রাজধানী কী?").skills == ()


def test_the_provider_for_each_agent_comes_from_the_router_not_the_agent() -> None:
    """§8.11: the agent names the kind of work; today's provider is the router's pick."""
    adapters = {n: FakeAdapter(n) for n in CFG.providers.providers}
    router = ProviderRouter(CFG.providers, adapters)
    expect = {"coding": "openai_codex", "planning": "ollama_local",
              "current_info": "google_antigravity", "quick_answer": "ollama_local",
              "vision": "ollama_local"}
    for text, *_ in CASES:
        a = route(text)
        if a.provider_task_type is not None:
            assert router.ranked(a.provider_task_type)[0].provider == \
                expect[a.provider_task_type], text
    # Codex down → the same Developer agent goes to the next coding provider (§8.11)
    adapters["openai_codex"] = FakeAdapter("openai_codex", state=HealthState.UNAVAILABLE)
    router = ProviderRouter(CFG.providers, adapters)
    names = [c.provider for c in router.ranked("coding")]
    assert names.index("google_antigravity") < names.index("ollama_local")


def test_roster_has_a_role_prompt_for_every_agent_and_project_specialist() -> None:
    for name, a in ROSTER.agents.items():
        assert (AGENTS_DIR / name / "AGENT.md").is_file() and a.prompt, name
    assert {"click-tv", "stream-doctor", "git-demo"} <= set(ROSTER.projects)
    # listed but without a profile yet → inactive, not an error
    assert "seo" in CFG.agents.project_agents and "seo" not in ROSTER.projects
    rules = ROSTER.projects["git-demo"].rules(CFG.root)
    assert "Git Demo" in rules and "python -m unittest" in rules
    assert "Click TV" not in rules                          # only THIS project's rules (§51)


def test_project_names_aliases_and_domain_keywords() -> None:
    assert find_project(CFG, "ক্লিক টিভি-র home page")[0] == "click-tv"     # type: ignore[index]
    assert find_project(CFG, "clicktv er scanner")[0] == "click-tv"         # type: ignore[index]
    assert find_project(CFG, "ei m3u8 link ta dekho")[0] == "stream-doctor"  # type: ignore[index]
    # a keyword is never enough to EDIT a project (coding uses names only)
    assert find_project(CFG, "ei m3u8 link ta dekho", keywords=False) is None
    assert find_project(CFG, "ajker abohawa kemon") is None


def test_project_rules_include_the_knowledge_folder(tmp_path: Path) -> None:
    from core.agents.roster import ProjectAgent
    from core.config.schema import ProjectProfile
    (tmp_path / "pk" / "demo").mkdir(parents=True)
    (tmp_path / "pk" / "demo" / "rules.md").write_text("Never change the player.",
                                                       encoding="utf-8")
    p = ProjectAgent("demo", "Demo Specialist", "Knows demo.",
                     ProjectProfile(name="Demo", knowledge_dir="pk/demo",
                                    approval_required_for=["deploy"]))
    rules = p.rules(tmp_path)
    assert "Knows demo." in rules and "Never change the player." in rules
    assert "deploy" in rules
