"""media skill (Phase 20, plan §37): real ffmpeg on a generated clip — every result is
a NEW file that ffmpeg can read back; the input is never touched."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.skills.api import PolicyDenied
from core.skills.mediaops import ffmpeg_exe
from tests.mocks.skills import SkillEnv, make_skill_env


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """4 s, 320x240, 25 fps test pattern + a 440 Hz tone."""
    out = tmp_path_factory.mktemp("media") / "clip.mp4"
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",  # noqa: S603
                    "testsrc=duration=4:size=320x240:rate=25", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=4", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
    return out


@pytest.fixture
def s(tmp_path: Path, clip: Path) -> tuple[SkillEnv, Path]:
    env = make_skill_env(tmp_path)
    work = env.workspace / "clip.mp4"
    work.write_bytes(clip.read_bytes())
    return env, work


def duration(env: SkillEnv, path: str) -> float:
    r = env.call("media", "info", {"input": path})
    assert r.ok, r.summary
    return float(r.data["duration_seconds"])


def test_info_reads_the_streams(s: tuple[SkillEnv, Path]) -> None:
    env, clip = s
    r = env.call("media", "info", {"input": str(clip)})
    assert r.ok and env.tasks.buttons == []                       # read-only, GREEN
    assert r.data["video"]["width"] == 320 and r.data["video"]["height"] == 240
    assert r.data["audio"]["codec"] == "aac" and 3.9 <= r.data["duration_seconds"] <= 4.1


def test_trim_cut_and_merge_change_the_length(s: tuple[SkillEnv, Path]) -> None:
    env, clip = s
    before = clip.read_bytes()
    t = env.call("media", "trim", {"input": str(clip), "start": "1", "end": "2.5"})
    assert t.ok and t.evidence["readable"] and abs(duration(env, t.data["output"]) - 1.5) < 0.2
    c = env.call("media", "cut", {"input": str(clip), "start": "1", "end": "2"})
    assert c.ok and abs(duration(env, c.data["output"]) - 3.0) < 0.25
    m = env.call("media", "merge", {"inputs": [str(clip), str(clip)]})
    assert m.ok and abs(duration(env, m.data["output"]) - 8.0) < 0.3
    assert clip.read_bytes() == before                              # the input is untouched


def test_picture_operations(s: tuple[SkillEnv, Path]) -> None:
    env, clip = s
    crop = env.call("media", "crop", {"input": str(clip), "width": 160, "height": 120})
    assert crop.ok and crop.data["info"]["video"]["width"] == 160
    res = env.call("media", "resize", {"input": str(clip), "width": 160})
    assert res.ok and res.data["info"]["video"]["height"] == 120   # aspect kept
    comp = env.call("media", "compress", {"input": str(clip), "quality": "small"})
    assert comp.ok and Path(comp.data["output"]).suffix == ".mp4"
    th = env.call("media", "thumbnail", {"input": str(clip), "at": "2"})
    assert th.ok and Path(th.data["output"]).suffix == ".jpg"
    fr = env.call("media", "frames", {"input": str(clip), "every_seconds": 1, "max_frames": 3})
    assert fr.ok and fr.evidence["count"] == 3


def test_audio_operations(s: tuple[SkillEnv, Path]) -> None:
    env, clip = s
    a = env.call("media", "extract_audio", {"input": str(clip), "format": "wav"})
    assert a.ok and "video" not in a.data["info"] and a.data["info"]["audio"]
    mp3 = env.call("media", "convert", {"input": str(clip), "to": "mp3"})
    assert mp3.ok and Path(mp3.data["output"]).suffix == ".mp3"
    n = env.call("media", "normalize_audio", {"input": str(clip)})
    assert n.ok and n.data["info"].get("video")                    # video kept as it was


def test_subtitles_are_burned_in(s: tuple[SkillEnv, Path]) -> None:
    env, clip = s
    srt = clip.with_name("subs.srt")
    srt.write_text("1\n00:00:00,500 --> 00:00:03,000\nNova test\n", encoding="utf-8")
    r = env.call("media", "subtitle_burn", {"input": str(clip), "subtitles": str(srt)})
    assert r.ok, r.summary


def test_never_overwrites_and_checks_its_input(s: tuple[SkillEnv, Path]) -> None:
    env, clip = s
    first = env.call("media", "thumbnail", {"input": str(clip)})
    second = env.call("media", "thumbnail", {"input": str(clip)})
    assert first.data["output"] != second.data["output"]           # _thumb, then _thumb_2
    with pytest.raises(PolicyDenied, match="already exists"):
        env.call("media", "thumbnail", {"input": str(clip), "output": first.data["output"]})
    with pytest.raises(PolicyDenied, match="already exists|new file"):   # the input itself
        env.call("media", "convert", {"input": str(clip), "to": "mp4", "output": str(clip)})
    with pytest.raises(PolicyDenied, match="seconds"):
        env.call("media", "trim", {"input": str(clip), "start": "soon"})
    with pytest.raises(PolicyDenied):
        env.call("media", "info", {"input": str(env.root / "secrets" / "x.mp4")})
    broken = clip.with_name("broken.mp4")
    broken.write_text("not a video", encoding="utf-8")
    r = env.call("media", "compress", {"input": str(broken)})
    assert not r.ok and not list(clip.parent.glob("broken_compressed*"))  # no half file
