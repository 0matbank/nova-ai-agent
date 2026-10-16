"""archive skill (Phase 20, plan §34): zip / unzip — and the unsafe archives are refused."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from core.skills.api import PolicyDenied
from tests.mocks.skills import SkillEnv, make_skill_env


@pytest.fixture
def s(tmp_path: Path) -> SkillEnv:
    env = make_skill_env(tmp_path)
    d = env.workspace / "photos"
    (d / "trip").mkdir(parents=True)
    (d / "a.txt").write_text("alpha", encoding="utf-8")
    (d / "trip" / "b.txt").write_text("beta", encoding="utf-8")
    return env


def test_zip_list_unzip_round_trip(s: SkillEnv) -> None:
    out = s.workspace / "photos.zip"
    z = s.call("archive", "zip", {"sources": [str(s.workspace / "photos")],
                                  "output": str(out)})
    assert z.ok and z.evidence["files"] == 2 and z.evidence["valid"] and s.tasks.buttons == []
    lst = s.call("archive", "list", {"archive": str(out)})
    assert {f["name"] for f in lst.data["files"]} == {"photos/a.txt", "photos/trip/b.txt"}
    u = s.call("archive", "unzip", {"archive": str(out),
                                    "destination": str(s.workspace / "restored")})
    assert u.ok and (s.workspace / "restored" / "photos" / "trip" / "b.txt").read_text(
        encoding="utf-8") == "beta"
    with pytest.raises(PolicyDenied, match="already has files"):     # never over files
        s.call("archive", "unzip", {"archive": str(out),
                                    "destination": str(s.workspace / "restored")})


def test_zip_slip_is_refused(s: SkillEnv) -> None:
    evil = s.workspace / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../escaped.txt", "gotcha")
    with pytest.raises(PolicyDenied, match="outside"):
        s.call("archive", "unzip", {"archive": str(evil)})
    assert not (s.workspace.parent / "escaped.txt").exists()


def test_zip_bomb_is_refused(s: SkillEnv) -> None:
    bomb = s.workspace / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", b"\0" * (60 * 1024 * 1024))
    with pytest.raises(PolicyDenied, match="zip bomb"):
        s.call("archive", "unzip", {"archive": str(bomb)})


def test_novas_secrets_are_never_packed(s: SkillEnv) -> None:
    with pytest.raises(PolicyDenied):
        s.call("archive", "zip", {"sources": [str(s.root / "secrets")],
                                  "output": str(s.workspace / "s.zip")})
