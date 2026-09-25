"""Every enabled skill ships real tests (plan §14). An empty tests/ folder
passes locally but is not tracked by git — CI then fails (Phase 11 lesson)."""

from __future__ import annotations

from pathlib import Path

from core.config import load_config

APP_DIR = Path(__file__).resolve().parents[2]


def test_every_enabled_skill_has_a_test_file() -> None:
    skills = load_config(APP_DIR / "config", APP_DIR.parent).skills.skills
    missing = [name for name, s in skills.items() if s.status == "enabled"
               and not list((APP_DIR / "skills" / name / "tests").glob("test_*.py"))]
    assert missing == []
