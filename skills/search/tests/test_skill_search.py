from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.mocks.skills import SkillEnv, make_skill_env


@pytest.fixture
def s(tmp_path: Path) -> SkillEnv:
    env = make_skill_env(tmp_path)
    w = env.workspace
    (w / "docs").mkdir()
    (w / "docs" / "invoice-2024.pdf").write_text("A" * 100)
    (w / "docs" / "copy-of-invoice.pdf").write_text("A" * 100)
    (w / "docs" / "notes.txt").write_text("hello")
    (env.root / "secrets" / "invoice-secret.pdf").write_text("A" * 100)
    old = w / "docs" / "old.txt"
    old.write_text("old")
    past = time.time() - 72 * 3600
    os.utime(old, (past, past))
    return env


def test_find_by_pattern_and_name(s: SkillEnv) -> None:
    r = s.call("search", "find", {"root": str(s.root), "pattern": "*.pdf"})
    names = {Path(m).name for m in r.data["matches"]}
    assert names == {"invoice-2024.pdf", "copy-of-invoice.pdf"}   # secrets/ skipped
    r = s.call("search", "find", {"root": str(s.workspace), "name_contains": "NOTES"})
    assert [Path(m).name for m in r.data["matches"]] == ["notes.txt"]
    assert r.untrusted and s.tasks.buttons == []


def test_recent(s: SkillEnv) -> None:
    r = s.call("search", "recent", {"root": str(s.workspace), "hours": 24})
    names = {Path(f["path"]).name for f in r.data["files"]}
    assert "notes.txt" in names and "old.txt" not in names


def test_duplicates(s: SkillEnv) -> None:
    r = s.call("search", "duplicates", {"root": str(s.root)})
    [group] = r.data["groups"]
    assert {Path(f).name for f in group["files"]} == {"invoice-2024.pdf", "copy-of-invoice.pdf"}
    assert "reclaimable" in r.summary


def test_scan_root_in_secrets_refused(s: SkillEnv) -> None:
    from core.skills.api import PolicyDenied
    with pytest.raises(PolicyDenied):
        s.call("search", "find", {"root": str(s.root / "secrets")})
