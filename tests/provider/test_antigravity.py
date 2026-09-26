"""Google Antigravity adapter (Phase 13): Google sign-in health via `agy models`,
Nova's safety rules in agy's settings, print mode with the prompt on stdin,
stream-json parsing (real agy 1.2.11 shapes), quota / auth detection and edit
permissions — against a fake CLI."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from providers.google_antigravity import AntigravityAdapter
from providers.google_antigravity import settings as agy_settings
from providers.google_antigravity.adapter import parse_stream
from providers.provider_base import ErrorCategory, HealthState, ProviderRequest

FAKE = str(Path(__file__).resolve().parents[1] / "mocks" / "fake_agy.py")


@pytest.fixture
def rules(tmp_path: Path) -> Path:
    """An agy settings file that already carries Nova's rules."""
    path = tmp_path / "agy-settings.json"
    agy_settings.ensure(path)
    return path


def agy(rules: Path, scratch: Path | None = None) -> AntigravityAdapter:
    return AntigravityAdapter(agy_cmd=[sys.executable, FAKE], scratch_dir=scratch,
                              settings_file=rules)


def req(**kw: object) -> ProviderRequest:
    fields: dict[str, object] = {"task_type": "coding", **kw}
    return ProviderRequest(task_id=1, user_request="fix the bug", **fields)  # type: ignore[arg-type]


# ----------------------------------------------------------------- health

@pytest.mark.parametrize(("mode", "state"), [("ok", HealthState.HEALTHY),
                                             ("signedout", HealthState.AUTH_REQUIRED)])
def test_health_is_the_google_sign_in(monkeypatch: pytest.MonkeyPatch, rules: Path, mode: str,
                                      state: HealthState) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", mode)
    assert asyncio.run(agy(rules).check_health()).state is state


def test_missing_cli_is_unavailable(tmp_path: Path, rules: Path) -> None:
    a = AntigravityAdapter(agy_cmd=[str(tmp_path / "agy.exe")], settings_file=rules)
    assert asyncio.run(a.check_health()).state is HealthState.UNAVAILABLE


def test_never_runs_without_novas_rules_in_agy_settings(tmp_path: Path) -> None:
    loose = tmp_path / "settings.json"
    loose.write_text(json.dumps({"toolPermission": "always-proceed"}), encoding="utf-8")
    h = asyncio.run(agy(loose).check_health())
    assert h.state is HealthState.UNAVAILABLE and "antigravity_setup" in h.detail


# --------------------------------------------------------------- settings

def test_setup_merges_rules_keeps_owner_settings_and_backs_up(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"trustedWorkspaces": ["D:\\x"], "toolPermission": "turbo",
                                "permissions": {"deny": ["command(rm)"]}}), encoding="utf-8")
    changed, backup = agy_settings.ensure(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert changed and backup is not None and backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8"))["toolPermission"] == "turbo"
    assert data["trustedWorkspaces"] == ["D:\\x"] and data["toolPermission"] == "request-review"
    assert data["useG1Credits"] is False                      # never spend paid credits
    deny = data["permissions"]["deny"]
    assert deny[0] == "command(rm)" and "command(git push)" in deny and "read_url(*)" in deny
    assert agy_settings.ensure(path) == (False, None)          # idempotent


def test_agy_dropping_default_valued_keys_still_counts_as_set(tmp_path: Path) -> None:
    """agy rewrites the file without keys equal to its defaults (seen live)."""
    assert agy_settings.missing({"permissions": {"deny": agy_settings.DENY}}) == []
    assert agy_settings.missing({"useG1Credits": True,
                                 "permissions": {"deny": agy_settings.DENY}}) == [
        "useG1Credits=False"]
    assert "read_url(*)" in agy_settings.missing({})


# --------------------------------------------------------------- complete

def test_edits_only_with_code_edit_and_never_skip_permissions(rules: Path) -> None:
    a = agy(rules)
    ro = a.args_for(req())
    no_perm = a.args_for(req(workspace="D:/p"))
    rw = a.args_for(req(workspace="D:/p", allowed_tools=("code.edit",)))
    for args in (ro, no_perm, rw):
        assert args[0] == "--print="                    # prompt on stdin, not as an argument
        assert args[args.index("--input-format") + 1] == "stream-json"
        assert "--dangerously-skip-permissions" not in args
    assert "accept-edits" not in ro and "accept-edits" not in no_perm
    assert rw[rw.index("--mode") + 1] == "accept-edits"


def test_result_is_normalised_and_prompt_goes_on_stdin(monkeypatch: pytest.MonkeyPatch,
                                                       tmp_path: Path, rules: Path) -> None:
    log = tmp_path / "call.json"
    monkeypatch.setenv("FAKE_AGY_MODE", "ok")
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    r = asyncio.run(agy(rules).complete(req(system="rules", context="ctx",
                                            workspace=str(tmp_path),
                                            allowed_tools=("code.edit",))))
    assert r.ok and r.provider == "google_antigravity"
    assert r.answer == "Fixed average() for empty lists." and r.session_id == "conv-42"
    assert r.files_changed == (str(tmp_path / "stats.py"),)
    assert r.commands == ()                                         # the refused one isn't
    assert any(e.get("error") for e in r.tool_events)
    assert "agy refused: command" in r.verification_hints
    assert r.usage.input_tokens == 900
    call = json.loads(log.read_text(encoding="utf-8"))
    content = json.loads(call["stdin"])["message"]["content"]
    assert content.startswith("IMPORTANT") and "never call run_command" in content
    assert "\n---\nrules\n" in content and content.endswith("fix the bug")
    assert "fix the bug" not in " ".join(call["argv"])
    assert Path(call["cwd"]) == tmp_path


def test_no_workspace_runs_in_an_empty_scratch_folder(monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path, rules: Path) -> None:
    log = tmp_path / "call.json"
    monkeypatch.setenv("FAKE_AGY_MODE", "text")
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    r = asyncio.run(agy(rules, tmp_path / "scratch").complete(req(task_type="summarization")))
    call = json.loads(log.read_text(encoding="utf-8"))
    assert r.ok and r.answer == "উত্তর এখানে।" and Path(call["cwd"]) == tmp_path / "scratch"
    assert call["argv"][call["argv"].index("--effort") + 1] == "low"


def test_quota_is_rate_limit_with_retry_after(monkeypatch: pytest.MonkeyPatch,
                                              rules: Path) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", "limit")
    a = agy(rules)
    r = asyncio.run(a.complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.RATE_LIMIT
    assert r.retry_after_seconds == 45 * 60
    monkeypatch.setenv("FAKE_AGY_MODE", "ok")
    assert asyncio.run(a.check_health()).state is HealthState.COOLDOWN     # router skips it


def test_auth_error_is_detected(monkeypatch: pytest.MonkeyPatch, rules: Path) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", "auth")
    r = asyncio.run(agy(rules).complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.AUTH


def test_crash_is_a_server_error_without_start_up_noise(monkeypatch: pytest.MonkeyPatch,
                                                        rules: Path) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", "crash")
    r = asyncio.run(agy(rules).complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.SERVER_ERROR
    assert "panic" in (r.error or "") and "google.Init" not in (r.error or "")


def test_answer_from_text_deltas_when_envelope_is_empty(monkeypatch: pytest.MonkeyPatch,
                                                        rules: Path) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", "deltas")
    r = asyncio.run(agy(rules).complete(req(task_type="reasoning")))
    assert r.ok and r.answer == "উত্তর এখানে।"


def test_refused_with_no_answer_is_flagged(monkeypatch: pytest.MonkeyPatch, rules: Path) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", "denied")
    r = asyncio.run(agy(rules).complete(req()))
    assert r.ok and r.answer == ""
    assert set(r.verification_hints) == {"empty answer", "agy refused: command"}


def test_parse_real_shapes_and_bare_envelope() -> None:
    real = "\n".join([
        '{"event":"init","conversation_id":"c1","init":{"cwd":"D:\\\\p","tools":[],'
        '"permission_mode":"request-review"}}',
        "not json",
        '{"event":"step_update","step_update":{"step_index":1,"state":"ACTIVE",'
        '"step_type":"agent_response","text_delta":"ঢা"}}',
        '{"event":"result","result":{"conversation_id":"c1","status":"SUCCESS",'
        '"response":"ঢাকা।\\n","usage":{"input_tokens":13100}}}'])
    out = parse_stream(real)
    assert out["answer"] == "ঢাকা।" and out["session_id"] == "c1" and out["result_seen"]
    assert out["usage"]["input_tokens"] == 13100
    bad = parse_stream(json.dumps({"status": "ERROR", "response": "", "error": "boom"}))
    assert bad["error"] == "boom" and bad["result_seen"]


def test_refused_command_gets_one_nudge_in_the_same_session(monkeypatch: pytest.MonkeyPatch,
                                                            tmp_path: Path, rules: Path) -> None:
    """Print mode ends the turn when a command is refused; the adapter tells the
    agent once more (stdin is still open) and it carries on with file tools."""
    log = tmp_path / "call.json"
    monkeypatch.setenv("FAKE_AGY_MODE", "nudge")
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    r = asyncio.run(agy(rules).complete(req(workspace=str(tmp_path),
                                            allowed_tools=("code.edit",))))
    nudge = json.loads(Path(str(log) + ".2").read_text(encoding="utf-8"))
    assert "run_command" in nudge["message"]["content"]
    assert r.ok and r.files_changed == ("a.py",)
    assert r.answer == "Done — see a.py."                  # file:/// links made plain
    assert "agy refused: command" in r.verification_hints
