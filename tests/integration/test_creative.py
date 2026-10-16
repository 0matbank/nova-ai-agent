"""Universal Creative Automation foundation (owner requirement 2026-10-16, Phase 20):
registry → footage analysis → structured plan (+ OpenTimelineIO) → FFmpeg render →
QC on the real output. Real ffmpeg; the Creative Director AI is scripted."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import load_config
from core.creative.analyze import analyze
from core.creative.plan import EditPlan, check_against, to_otio
from core.creative.qc import check_render
from core.creative.registry import CreativeConfig, SoftwareRegistry, load
from core.creative.render import render
from core.memory import ProjectMemory
from core.orchestrator.executor import UserRequestExecutor
from core.queue.states import TaskState
from core.router.intent import IntentRouter, classify_by_rules
from core.skills.mediaops import ffmpeg_exe
from models.router import ProviderRouter
from providers.provider_base import HealthState
from tests.mocks.providers import FakeAdapter
from tests.mocks.skills import make_skill_env


def make_clip(path: Path, seconds: int = 6, size: str = "1280x720") -> Path:
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",  # noqa: S603
                    f"testsrc=duration={seconds}:size={size}:rate=25", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def black_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """red 2 s · black 1 s · blue 2 s; tone · 1.5 s silence · tone."""
    out = tmp_path_factory.mktemp("cr") / "mixed.mp4"
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error",  # noqa: S603
                    "-f", "lavfi", "-i", "color=red:s=320x240:d=2:r=25",
                    "-f", "lavfi", "-i", "color=black:s=320x240:d=1:r=25",
                    "-f", "lavfi", "-i", "color=blue:s=320x240:d=2:r=25",
                    "-f", "lavfi", "-i", "sine=f=440:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1.5",
                    "-f", "lavfi", "-i", "sine=f=660:d=1.5", "-filter_complex",
                    "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v];[3:a]aresample=44100[a0];"
                    "[4:a]aresample=44100[a1];[5:a]aresample=44100[a2];"
                    "[a0][a1][a2]concat=n=3:v=0:a=1[a]", "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


# ----------------------------------------------------------------- registry

def test_registry_is_honest_and_detects_ffmpeg() -> None:
    reg = SoftwareRegistry.load()
    assert reg.installed()["ffmpeg"].found
    assert reg.choose({"cut", "captions", "aspect_reframe", "export"}) == ("ffmpeg", [])
    adapter, missing = reg.choose({"cut", "three_d"})
    assert adapter is None and missing == ["three_d"]           # nobody can: said so
    for key, s in reg.config.software.items():
        if key != "ffmpeg":                                     # only proven work is tested
            assert "tested" not in s.capabilities.values(), key


def test_registry_config_rejects_unknown_capabilities() -> None:
    data = json.loads(load().model_dump_json())
    data["software"]["ffmpeg"]["capabilities"]["teleport"] = "tested"
    with pytest.raises(ValidationError, match="teleport"):
        CreativeConfig.model_validate(data)


# ----------------------------------------------------------------- analysis

def test_footage_analysis_finds_black_silence_and_cuts(black_clip: Path) -> None:
    r = asyncio.run(analyze(black_clip))
    assert abs(r.duration - 5.0) < 0.1 and r.has_audio and (r.width, r.height) == (320, 240)
    assert any(abs(a - 2.0) < 0.2 and abs(b - 3.0) < 0.2 for a, b in r.black)
    assert any(abs(a - 2.0) < 0.2 and abs(b - 3.5) < 0.3 for a, b in r.silences)
    assert any(abs(t - 2.0) < 0.2 for t in r.scenes)
    assert "black 2.0" in r.summary()


# ----------------------------------------------------------------- plan

def plan_json(src: str, **look: object) -> dict:
    return {"title": "reel", "sources": [src],
            "clips": [{"source": 0, "start": 3, "end": 5}, {"source": 0, "start": 0.5,
                                                            "end": 2.5}],
            "captions": [{"start": 0.2, "end": 1.8, "text": "নোভা Nova"}],
            "look": {"aspect": "9:16", "fade_in": 0.4, "fade_out": 0.4, "transitions": "dip",
                     **look}}


def test_plan_validation_and_capabilities(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "a.mp4")
    p = EditPlan.model_validate(plan_json(str(clip)))
    assert p.total_seconds() == 4.0
    assert {"cut", "reorder", "captions", "aspect_reframe", "transitions"} <= \
        p.needed_capabilities()
    bad = plan_json(str(clip))
    bad["clips"][0]["end"] = 60
    rep = asyncio.run(analyze(clip))
    assert "only 6.00s long" in check_against(EditPlan.model_validate(bad), [rep])[0]
    cap = plan_json(str(clip))
    cap["captions"][0]["start"] = 50
    with pytest.raises(ValidationError, match="outside"):
        EditPlan.model_validate(cap)


def test_plan_exports_an_opentimelineio_timeline(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "a.mp4")
    otio = to_otio(EditPlan.model_validate(plan_json(str(clip))), fps=25)
    assert otio["OTIO_SCHEMA"] == "Timeline.1"
    track = otio["tracks"]["children"][0]
    kinds = [c["OTIO_SCHEMA"] for c in track["children"]]
    assert kinds == ["Clip.2", "Transition.1", "Clip.2"]
    first = track["children"][0]
    assert first["source_range"]["start_time"] == {"OTIO_SCHEMA": "RationalTime.1",
                                                   "rate": 25, "value": 75.0}
    assert first["media_references"]["DEFAULT_MEDIA"]["target_url"].startswith("file:")
    assert track["markers"][0]["name"] == "নোভা Nova"            # caption kept as a marker
    assert otio["metadata"]["nova"]["plan"]["look"]["aspect"] == "9:16"   # nothing lost


# ----------------------------------------------------------------- render + QC

def test_render_and_qc_measure_the_real_result(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "a.mp4")
    rep = asyncio.run(analyze(clip))
    plan = EditPlan.model_validate(plan_json(str(clip)))
    before = clip.read_bytes()
    r = asyncio.run(render(plan, [rep], tmp_path / "out.mp4"))
    assert r.ok, r.error
    assert r.size == (1080, 1920) and r.plan_file.is_file() and r.otio_file.is_file()
    assert clip.read_bytes() == before                           # non-destructive
    q = asyncio.run(check_render(r.output, plan, r.size, [rep]))
    assert q.ok, q.lines()
    names = [n for n, _, _ in q.checks]
    assert names == ["length", "size", "no unplanned black", "video moves", "audio present",
                     "no clipping"]
    # QC is not a rubber stamp: a wrong frame size / length is caught
    wrong = asyncio.run(check_render(r.output, plan, (1920, 1080), [rep]))
    assert not wrong.ok and any(n == "size" and not ok for n, ok, _ in wrong.checks)
    longer = plan.model_copy(update={"clips": [*plan.clips, plan.clips[0]]})
    assert not asyncio.run(check_render(r.output, longer, r.size, [rep])).ok


def test_qc_catches_a_result_that_looks_like_a_still_picture(tmp_path: Path) -> None:
    """Owner 2026-10-16: "video cholaii nah, stop picture" — a moving source rendered
    into a still must fail QC, even though the file, length and size are right."""
    clip = make_clip(tmp_path / "a.mp4")
    rep = asyncio.run(analyze(clip))
    assert not rep.frozen                                     # testsrc moves
    plan = EditPlan.model_validate({"sources": [str(clip)],
                                    "clips": [{"source": 0, "start": 0, "end": 4}]})
    still = tmp_path / "still.mp4"
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", "1", "-i",  # noqa: S603
                    str(clip), "-frames:v", "1", str(tmp_path / "f.png")], check=True)
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-loop", "1", "-i",  # noqa: S603
                    str(tmp_path / "f.png"), "-i", str(clip), "-map", "0:v", "-map", "1:a",
                    "-t", "4", "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(still)], check=True)
    q = asyncio.run(check_render(still, plan, (1280, 720), [rep]))
    assert not q.ok and any(n == "video moves" and not ok for n, ok, _ in q.checks), q.lines()


def test_background_music_is_mixed_under_a_muted_clip(tmp_path: Path) -> None:
    """The registry says music / audio_mix are `tested` — this is that proof."""
    clip = make_clip(tmp_path / "a.mp4")
    music = tmp_path / "song.mp3"
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",  # noqa: S603
                    "sine=frequency=220:duration=2", str(music)], check=True)  # shorter: loops
    rep = asyncio.run(analyze(clip))
    plan = EditPlan.model_validate({
        "sources": [str(clip)], "clips": [{"source": 0, "start": 0, "end": 5}],
        "audio": {"mute_original": True, "music": str(music), "music_volume": 0.5}})
    assert {"music", "audio_mix"} <= plan.needed_capabilities()
    r = asyncio.run(render(plan, [rep], tmp_path / "m.mp4"))
    assert r.ok, r.error
    q = asyncio.run(check_render(r.output, plan, r.size, [rep]))
    assert q.ok, q.lines()
    out = asyncio.run(analyze(r.output))
    assert out.has_audio and not out.silences                   # music under all 5 s


# ----------------------------------------------------------------- the whole flow

def test_creative_request_end_to_end(tmp_path: Path) -> None:
    s = make_skill_env(tmp_path)
    clip = make_clip(s.workspace / "trip.mp4", seconds=8)
    first = plan_json(str(clip))
    first["clips"][0]["end"] = 30                                 # beyond the footage
    good = plan_json(str(clip))
    director = FakeAdapter("google_antigravity", [json.dumps(first), json.dumps(good)])
    cfg = load_config()
    ad = {n: FakeAdapter(n, state=HealthState.UNAVAILABLE) for n in cfg.providers.providers}
    ad["google_antigravity"] = director
    ad["ollama_local"] = FakeAdapter("ollama_local", [])
    router = ProviderRouter(cfg.providers, ad)
    memory = ProjectMemory(s.tasks.store._sessions)
    s.tasks.engine.register("user_request", UserRequestExecutor(
        IntentRouter(router), router, s.runner, memory=memory))

    def submit(text: str) -> int:
        tid = int(s.tasks.store.create(title=text, request_text=text,
                                       task_type="user_request", channel="telegram",
                                       chat_id="555").id)
        for _ in range(4):
            if not asyncio.run(s.tasks.engine.run_once()):
                break
        return tid
    style = submit("amar edit style mone rakho: cut druto, caption boro")
    assert "edit-style মনে রাখলাম" in s.tasks.store.get(style).result_summary
    tid = submit(f"{clip} theke instagram er jonno ekta reel banao, caption dao")
    t = s.tasks.store.get(tid)
    assert t.state is TaskState.COMPLETED, t.error_message
    reqs = director.requests
    assert [r.task_type for r in reqs] == ["reasoning", "reasoning"]
    assert "only 8.00s long" in reqs[1].previous_attempt                # repair round
    assert "cut druto" in reqs[0].system                                 # style memory used
    assert "scene" in reqs[0].context or "8.0s" in reqs[0].context       # footage facts
    out = clip.with_name("trip_nova_edit.mp4")
    assert out.is_file() and out.with_suffix(".otio").is_file()
    done = next(m for _, m in s.tasks.sent if f"TASK #{tid} COMPLETE" in m)
    assert "✅ PASS" in done and "QC length" in done and "🧭 Creative Director" in done
    assert any("[video:trip_nova_edit.mp4]" in m for _, m in s.tasks.sent)  # on the phone


@pytest.mark.parametrize(("text", "category"), [
    (r"D:\v\trip.mp4 theke 30 second er reel banao", "creative"),
    ("ei video ta cinematic edit koro", "creative"),
    (r"D:\v\clip.mp4 video ta theke thumbnail banao", "media"),
    ("ভিডিওটা থেকে অডিও আলাদা করো", "media"),
    ("amar facebook page e ekta reel post koro", "social"),
])
def test_creative_vs_single_media_operation(text: str, category: str) -> None:
    hit = classify_by_rules(text)
    assert hit is not None and hit.category == category
