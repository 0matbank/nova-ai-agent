"""Google Antigravity adapter (Phase 13): Google sign-in health via `agy models`,
print mode with the prompt on stdin, stream-json result parsing, quota / auth
detection, sandbox and edit permissions — against a fake CLI."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from providers.google_antigravity import AntigravityAdapter
from providers.google_antigravity.adapter import parse_stream
from providers.provider_base import ErrorCategory, HealthState, ProviderRequest

FAKE = str(Path(__file__).resolve().parents[1] / "mocks" / "fake_agy.py")


def agy(scratch: Path | None = None) -> AntigravityAdapter:
    return AntigravityAdapter(agy_cmd=[sys.executable, FAKE], scratch_dir=scratch)


def req(**kw: object) -> ProviderRequest:
    fields: dict[str, object] = {"task_type": "coding", **kw}
    return ProviderRequest(task_id=1, user_request="fix the bug", **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(("mode", "state"), [("ok", HealthState.HEALTHY),
                                             ("signedout", HealthState.AUTH_REQUIRED)])
def test_health_is_the_google_sign_in(monkeypatch: pytest.MonkeyPatch, mode: str,
                                      state: HealthState) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", mode)
    assert asyncio.run(agy().check_health()).state is state


def test_missing_cli_is_unavailable(tmp_path: Path) -> None:
    a = AntigravityAdapter(agy_cmd=[str(tmp_path / "agy.exe")])
    assert asyncio.run(a.check_health()).state is HealthState.UNAVAILABLE


def test_always_sandboxed_and_edits_only_with_code_edit() -> None:
    a = agy()
    ro = a.args_for(req())
    rw_no_perm = a.args_for(req(workspace="D:/p"))
    rw = a.args_for(req(workspace="D:/p", allowed_tools=("code.edit",)))
    for args in (ro, rw_no_perm, rw):
        assert "--sandbox" in args and "--print" in args
        assert "--dangerously-skip-permissions" not in args
        assert args[args.index("--input-format") + 1] == "stream-json"   # prompt via stdin
    assert "accept-edits" not in ro and "accept-edits" not in rw_no_perm
    assert rw[rw.index("--mode") + 1] == "accept-edits"


def test_result_is_normalised_and_prompt_goes_on_stdin(monkeypatch: pytest.MonkeyPatch,
                                                       tmp_path: Path) -> None:
    log = tmp_path / "call.json"
    monkeypatch.setenv("FAKE_AGY_MODE", "ok")
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    r = asyncio.run(agy().complete(req(system="rules", context="ctx", workspace=str(tmp_path),
                                       allowed_tools=("code.edit",))))
    assert r.ok and r.provider == "google_antigravity"
    assert r.answer == "Fixed average() for empty lists." and r.session_id == "conv-42"
    assert r.files_changed == ("stats.py",) and r.commands == ("python -m unittest",)
    assert r.usage.input_tokens == 900 and r.model == "gemini-3.8-pro-high"
    call = json.loads(log.read_text(encoding="utf-8"))
    msg = json.loads(call["stdin"])
    assert msg["event"] == "user" and msg["message"]["content"].endswith("fix the bug")
    assert "rules" in msg["message"]["content"]
    assert "fix the bug" not in " ".join(call["argv"])          # never on the command line
    assert Path(call["cwd"]) == tmp_path


def test_no_workspace_runs_in_an_empty_scratch_folder(monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path) -> None:
    log = tmp_path / "call.json"
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    r = asyncio.run(agy(tmp_path / "scratch").complete(req(task_type="summarization")))
    call = json.loads(log.read_text(encoding="utf-8"))
    assert r.ok and Path(call["cwd"]) == tmp_path / "scratch"
    assert call["argv"][call["argv"].index("--effort") + 1] == "low"


def test_quota_is_rate_limit_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", "limit")
    a = agy()
    r = asyncio.run(a.complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.RATE_LIMIT
    assert r.retry_after_seconds == 45 * 60
    monkeypatch.setenv("FAKE_AGY_MODE", "ok")
    assert asyncio.run(a.check_health()).state is HealthState.COOLDOWN     # router skips it


def test_auth_error_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", "auth")
    r = asyncio.run(agy().complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.AUTH


def test_crash_is_a_server_error_without_start_up_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", "crash")
    r = asyncio.run(agy().complete(req()))
    assert not r.ok and r.error_category is ErrorCategory.SERVER_ERROR
    assert "panic" in (r.error or "") and "google.Init" not in (r.error or "")


def test_answer_from_text_deltas_when_envelope_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_AGY_MODE", "deltas")
    r = asyncio.run(agy().complete(req(task_type="reasoning")))
    assert r.ok and r.answer == "উত্তর এখানে।"


def test_parse_single_json_envelope_and_junk() -> None:
    out = parse_stream("not json\n[1]\n" + json.dumps(
        {"conversation_id": "c1", "status": "SUCCESS", "response": " hi ", "usage": {}}))
    assert out["answer"] == "hi" and out["session_id"] == "c1" and out["result_seen"]
    bad = parse_stream(json.dumps({"status": "ERROR", "response": "", "error": "boom"}))
    assert bad["error"] == "boom" and bad["status"] == "ERROR"
