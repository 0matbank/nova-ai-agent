"""image skill (Phase 20): info + resize / convert / compress into a new file."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.skills.api import PolicyDenied
from tests.mocks.skills import SkillEnv, make_skill_env


@pytest.fixture
def s(tmp_path: Path) -> tuple[SkillEnv, Path]:
    from PIL import Image
    env = make_skill_env(tmp_path)
    img = env.workspace / "pic.png"
    Image.new("RGBA", (400, 200), (200, 30, 30, 255)).save(img)
    return env, img


def test_info_and_resize_keeps_the_aspect(s: tuple[SkillEnv, Path]) -> None:
    env, img = s
    i = env.call("image", "info", {"input": str(img)})
    assert i.ok and (i.data["width"], i.data["height"]) == (400, 200)
    r = env.call("image", "process", {"input": str(img), "width": 100})
    assert r.ok and r.evidence["exists"]
    assert env.call("image", "info", {"input": r.data["output"]}).data["height"] == 50


def test_convert_to_jpg_and_never_overwrite(s: tuple[SkillEnv, Path]) -> None:
    env, img = s
    r = env.call("image", "process", {"input": str(img), "to": "jpg", "quality": "small"})
    assert r.ok and Path(r.data["output"]).suffix == ".jpg"           # RGBA → RGB handled
    with pytest.raises(PolicyDenied, match="already exists"):
        env.call("image", "process", {"input": str(img), "output": str(img)})
    with pytest.raises(PolicyDenied, match="format"):
        env.call("image", "process", {"input": str(img), "to": "exe"})
