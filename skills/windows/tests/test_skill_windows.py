from __future__ import annotations

from pathlib import Path

import pytest

from tests.mocks.skills import SkillEnv, make_skill_env


@pytest.fixture
def s(tmp_path: Path) -> SkillEnv:
    return make_skill_env(tmp_path)


def test_status(s: SkillEnv) -> None:
    r = s.call("windows", "status", {})
    assert r.ok and 0 < r.data["ram_percent"] <= 100 and "CPU:" in r.summary


def test_processes(s: SkillEnv) -> None:
    r = s.call("windows", "processes", {"limit": 5})
    assert r.ok and 0 < len(r.data["processes"]) <= 5
    mems = [p["memory_mb"] for p in r.data["processes"]]
    assert mems == sorted(mems, reverse=True)


def test_system_info(s: SkillEnv) -> None:
    r = s.call("windows", "system_info", {})
    assert r.ok and r.data["cpu_count"] >= 1 and s.tasks.buttons == []
