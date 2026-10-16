"""FFmpeg helpers for the media skill (plan §37, Phase 20). Deterministic — no LLM
tokens are spent on media work. The ffmpeg binary comes pinned with the
`imageio-ffmpeg` package (inside the venv, nothing installed on the system).

Safety: the input is only read; the result is always a NEW file (ffmpeg -n never
overwrites), next to the input unless the owner names another place; paths go
through the same PathPolicy as the files skill."""

from __future__ import annotations

import asyncio
import re
from functools import cache
from pathlib import Path
from typing import Any

from core.skills.api import PolicyDenied, ToolEnv, ToolResult

TIME_RX = re.compile(r"^\d+(?:\.\d+)?$|^\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?$")
MAX_SECONDS = 1800
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO = re.compile(r"Stream #\S+.*?: Video: (\w+).*?, (\d{2,5})x(\d{2,5})(?:.*?, ([\d.]+) fps)?")
_AUDIO = re.compile(r"Stream #\S+.*?: Audio: (\w+)(?:.*?, (\d+) Hz)?(?:, ([\w.() ]+?))?(?:,|$)",
                    re.MULTILINE)
_BITRATE = re.compile(r"bitrate:\s*(\d+) kb/s")


@cache
def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return str(imageio_ffmpeg.get_ffmpeg_exe())


def check_time(value: str | None, name: str) -> None:
    if value is not None and not TIME_RX.match(value.strip()):
        raise PolicyDenied(f"{name} must be seconds (90) or a clock time (1:30, 00:01:30.5)")


def seconds(value: str) -> float:
    parts = [float(x) for x in value.strip().split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def input_file(env: ToolEnv, raw: str) -> Path:
    src = env.paths.resolve(raw)
    env.paths.check_read(src)
    if not src.is_file():
        raise PolicyDenied(f"{src} is not a file")
    return src


def output_file(env: ToolEnv, src: Path, raw: str | None, suffix: str, ext: str) -> Path:
    """A new file — never the input, never an existing file."""
    if raw:
        out = env.paths.resolve(raw)
    else:
        out = src.with_name(f"{src.stem}_{suffix}{ext}")
        n = 2
        while out.exists():
            out = src.with_name(f"{src.stem}_{suffix}_{n}{ext}")
            n += 1
    env.paths.check_write(out)
    if out.exists():
        raise PolicyDenied(f"{out} already exists — choose another output name")
    if out.resolve() == src.resolve():
        raise PolicyDenied("the output must be a new file, not the input")
    return out


async def run_ffmpeg(args: list[str], timeout: float = 900) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-n", "-loglevel", "error", *args,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False, f"ffmpeg timed out after {timeout:.0f}s"
    return proc.returncode == 0, err.decode("utf-8", "replace")[-1500:]


async def probe(path: Path) -> dict[str, Any]:
    """Duration, streams, resolution, bitrate — from ffmpeg's own report."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    _, err = await asyncio.wait_for(proc.communicate(), 60)
    text = err.decode("utf-8", "replace")
    info: dict[str, Any] = {"path": str(path), "size": path.stat().st_size}
    if (m := _DURATION.search(text)) is not None:
        info["duration_seconds"] = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60
                                         + float(m.group(3)), 2)
    if (m := _BITRATE.search(text)) is not None:
        info["bitrate_kbps"] = int(m.group(1))
    if (m := _VIDEO.search(text)) is not None:
        info["video"] = {"codec": m.group(1), "width": int(m.group(2)),
                         "height": int(m.group(3)),
                         "fps": float(m.group(4)) if m.group(4) else None}
    if (m := _AUDIO.search(text)) is not None:
        info["audio"] = {"codec": m.group(1),
                         "sample_rate": int(m.group(2)) if m.group(2) else None,
                         "channels": (m.group(3) or "").strip() or None}
    if "duration_seconds" not in info and "video" not in info and "audio" not in info:
        info["error"] = "not a media file ffmpeg can read"
    return info


async def produce(src: Path, out: Path, args: list[str], what: str) -> ToolResult:
    """Run ffmpeg into `out` and prove the result: the new file exists and ffmpeg can
    read it back (plan §54)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ok, err = await run_ffmpeg(args)
    if not ok or not out.is_file():
        out.unlink(missing_ok=True)             # no half-written file left behind
        return ToolResult(False, f"{what} failed: {err.strip()[-400:] or 'no output'}",
                          {"input": str(src)}, {"exists": False})
    info = await probe(out)
    return ToolResult(True, f"{what}: {out} ({out.stat().st_size / 1024 / 1024:.1f} MB"
                            + (f", {info['duration_seconds']}s" if "duration_seconds" in info
                               else "") + ")",
                      {"input": str(src), "output": str(out), "info": info},
                      {"output": str(out), "exists": True, "size": out.stat().st_size,
                       "readable": "error" not in info})
