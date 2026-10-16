"""FFmpeg adapter (Creative pipeline step 5): render an EditPlan into a NEW video,
non-destructively (sources are only read). Next to the video Nova keeps the plan
(.plan.json) and an OpenTimelineIO timeline (.otio), so the edit can be changed and
rendered again — or opened in another editor."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.creative.analyze import FootageReport
from core.creative.plan import ASPECTS, EditPlan, to_otio
from core.skills.mediaops import run_ffmpeg

CRF = {"high": "20", "medium": "24", "small": "29"}
FPS = 30
# a font with Bangla glyphs for captions on Windows (libass falls back otherwise)
CAPTION_FONT = "Nirmala UI" if sys.platform == "win32" else "DejaVu Sans"


@dataclass
class RenderResult:
    ok: bool
    output: Path
    plan_file: Path
    otio_file: Path
    error: str = ""
    size: tuple[int, int] = (0, 0)


def target_size(plan: EditPlan, reports: list[FootageReport]) -> tuple[int, int]:
    if plan.look.aspect:
        return ASPECTS[plan.look.aspect]
    first = next((r for r in reports if r.width), None)
    w, h = (first.width or 1280, first.height or 720) if first else (1280, 720)
    return w - w % 2, h - h % 2


def _srt(plan: EditPlan) -> str:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i}\n{ts(c.start)} --> {ts(min(c.end, plan.total_seconds()))}\n{c.text}\n"
                     for i, c in enumerate(plan.captions, start=1))


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def build_graph(plan: EditPlan, reports: list[FootageReport], size: tuple[int, int],
                srt: Path | None, music_input: int | None) -> tuple[str, list[str]]:
    w, h = size
    parts: list[str] = []
    n = len(plan.clips)
    dip = plan.look.transitions == "dip"
    half = plan.look.transition_seconds / 2
    reframe = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
               if plan.look.fit == "crop" else
               f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black")
    pairs = ""
    for i, c in enumerate(plan.clips):
        L = c.length
        vf = [f"trim=start={c.start}:end={c.end}", "setpts=PTS-STARTPTS", reframe, "setsar=1",
              f"fps={FPS}", "format=yuv420p"]
        af = [f"atrim=start={c.start}:end={c.end}", "asetpts=PTS-STARTPTS",
              "aresample=48000", "aformat=channel_layouts=stereo"]
        if dip and i > 0:
            vf.append(f"fade=t=in:st=0:d={half}")
            af.append(f"afade=t=in:st=0:d={half}")
        if dip and i < n - 1:
            vf.append(f"fade=t=out:st={max(0.0, L - half):.3f}:d={half}")
            af.append(f"afade=t=out:st={max(0.0, L - half):.3f}:d={half}")
        parts.append(f"[{c.source}:v]{','.join(vf)}[v{i}]")
        if reports[c.source].has_audio and not plan.audio.mute_original:
            parts.append(f"[{c.source}:a]{','.join(af)}[a{i}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{L:.3f},asetpts=PTS-STARTPTS"
                         f"[a{i}]")
        pairs += f"[v{i}][a{i}]"
    parts.append(f"{pairs}concat=n={n}:v=1:a=1[cv][ca]")
    total = plan.total_seconds()
    vpost, apost = [], []
    if plan.look.fade_in:
        vpost.append(f"fade=t=in:st=0:d={plan.look.fade_in}")
        apost.append(f"afade=t=in:st=0:d={plan.look.fade_in}")
    if plan.look.fade_out:
        st = max(0.0, total - plan.look.fade_out)
        vpost.append(f"fade=t=out:st={st:.3f}:d={plan.look.fade_out}")
        apost.append(f"afade=t=out:st={st:.3f}:d={plan.look.fade_out}")
    if srt is not None:
        vpost.append(f"subtitles='{_filter_path(srt)}':force_style='FontName={CAPTION_FONT},"
                     "FontSize=16,Outline=2,Shadow=1,MarginV=40'")
    parts.append(f"[cv]{','.join(vpost) or 'null'}[vout]")
    audio_in = "[ca]"
    if music_input is not None:
        parts.append(f"[{music_input}:a]aresample=48000,aformat=channel_layouts=stereo,"
                     f"volume={plan.audio.music_volume},atrim=0:{total:.3f}[mus]")
        parts.append("[ca][mus]amix=inputs=2:duration=first:normalize=0[mix]")
        audio_in = "[mix]"
    if plan.audio.normalize:
        apost.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    apost.append("aresample=48000")
    parts.append(f"{audio_in}{','.join(apost)}[aout]")
    return ";".join(parts), ["-map", "[vout]", "-map", "[aout]"]


async def render(plan: EditPlan, reports: list[FootageReport], output: Path) -> RenderResult:
    plan_file = output.with_suffix(".plan.json")
    otio_file = output.with_suffix(".otio")
    size = target_size(plan, reports)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nova-render-") as tmp:
        srt = None
        if plan.captions:
            srt = Path(tmp) / "captions.srt"
            srt.write_text(_srt(plan), encoding="utf-8")
        inputs: list[str] = []
        for src in plan.sources:
            inputs += ["-i", src]
        music_input = None
        if plan.audio.music:
            music_input = len(plan.sources)
            inputs += ["-stream_loop", "-1", "-i", plan.audio.music]
        graph, maps = build_graph(plan, reports, size, srt, music_input)
        codec = (["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-c:a", "libopus"]
                 if plan.export.format == "webm" else
                 ["-c:v", "libx264", "-preset", "medium", "-crf", CRF[plan.export.quality],
                  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"])
        ok, err = await run_ffmpeg([*inputs, "-filter_complex", graph, *maps, *codec,
                                    "-t", f"{plan.total_seconds():.3f}", str(output)],
                                   timeout=max(900.0, plan.total_seconds() * 20))
    if not ok or not output.is_file():
        output.unlink(missing_ok=True)
        return RenderResult(False, output, plan_file, otio_file, err.strip()[-800:], size)
    plan_file.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    otio_file.write_text(json.dumps(to_otio(plan, FPS, str(output)), indent=2,
                                    ensure_ascii=False), encoding="utf-8")
    return RenderResult(True, output, plan_file, otio_file, "", size)
