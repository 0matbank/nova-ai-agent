"""Footage analysis (Creative pipeline step 2): what is in a clip, measured by ffmpeg
— no AI tokens. The Creative Director plans from these facts, and QC uses the same
measures on the result.

  scenes     shot changes (scene score > threshold) → good cut points
  silences   quiet stretches (< -35 dB for ≥ 0.6 s) → dead air to trim
  black      black stretches (≥ 0.4 s) → empty frames
  loudness   mean / max volume (dB) → clipping (max ≥ -0.1 dB) or near silence
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.skills.mediaops import ffmpeg_exe, probe

_PTS = re.compile(r"pts_time:([\d.]+)")
_SIL_START = re.compile(r"silence_start: ([\d.]+)")
_SIL_END = re.compile(r"silence_end: ([\d.]+)")
_BLACK = re.compile(r"black_start:([\d.]+) black_end:([\d.]+)")
_MEAN = re.compile(r"mean_volume: (-?[\d.]+|-inf) dB")
_MAX = re.compile(r"max_volume: (-?[\d.]+|-inf) dB")


@dataclass
class FootageReport:
    path: str
    duration: float
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool = False
    scenes: list[float] = field(default_factory=list)
    silences: list[tuple[float, float]] = field(default_factory=list)
    black: list[tuple[float, float]] = field(default_factory=list)
    mean_db: float | None = None
    max_db: float | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        """Compact facts for the planner (§58: no raw dumps)."""
        parts = [f"{Path(self.path).name}: {self.duration:.1f}s"]
        if self.width:
            parts.append(f"{self.width}x{self.height} @ {self.fps or '?'} fps")
        parts.append("audio" if self.has_audio else "no audio")
        if self.scenes:
            parts.append("scene changes at " + ", ".join(f"{t:.1f}s" for t in self.scenes[:25]))
        if self.silences:
            parts.append("silent " + ", ".join(f"{a:.1f}–{b:.1f}s" for a, b in self.silences[:15]))
        if self.black:
            parts.append("black " + ", ".join(f"{a:.1f}–{b:.1f}s" for a, b in self.black[:15]))
        if self.mean_db is not None:
            parts.append(f"loudness mean {self.mean_db} dB, max {self.max_db} dB")
        return "; ".join(parts)


async def _stderr(args: list[str], timeout: float = 600) -> str:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe(), "-hide_banner", "-nostdin", *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return ""
    return err.decode("utf-8", "replace")


def _db(m: re.Match[str] | None) -> float | None:
    if m is None:
        return None
    return -120.0 if m.group(1) == "-inf" else float(m.group(1))


async def analyze(path: Path, scene_threshold: float = 0.35) -> FootageReport:
    info = await probe(path)
    rep = FootageReport(str(path), float(info.get("duration_seconds") or 0.0),
                        has_audio="audio" in info)
    if "video" in info:
        v = info["video"]
        rep.width, rep.height, rep.fps = v["width"], v["height"], v.get("fps")
        out = await _stderr(["-i", str(path), "-an", "-vf",
                             f"select='gt(scene,{scene_threshold})',showinfo,"
                             "blackdetect=d=0.4:pix_th=0.10", "-f", "null", "-"])
        rep.scenes = [round(float(t), 2) for t in _PTS.findall(out)]
        rep.black = [(round(float(a), 2), round(float(b), 2)) for a, b in _BLACK.findall(out)]
        if not rep.black:                    # blackdetect after select sees only scene frames
            out = await _stderr(["-i", str(path), "-an", "-vf",
                                 "blackdetect=d=0.4:pix_th=0.10", "-f", "null", "-"])
            rep.black = [(round(float(a), 2), round(float(b), 2))
                         for a, b in _BLACK.findall(out)]
    if rep.has_audio:
        out = await _stderr(["-i", str(path), "-vn", "-af",
                             "silencedetect=n=-35dB:d=0.6,volumedetect", "-f", "null", "-"])
        starts = [float(x) for x in _SIL_START.findall(out)]
        ends = [float(x) for x in _SIL_END.findall(out)]
        rep.silences = [(round(a, 2), round(ends[i] if i < len(ends) else rep.duration, 2))
                        for i, a in enumerate(starts)]
        rep.mean_db, rep.max_db = _db(_MEAN.search(out)), _db(_MAX.search(out))
    return rep
