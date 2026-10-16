"""media.compress (plan §37)."""

from __future__ import annotations

from typing import Literal

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.mediaops import input_file, output_file, produce

CRF = {"high": "23", "medium": "28", "small": "32"}


class P(Params):
    input: str
    quality: Literal["high", "medium", "small"] = "medium"
    output: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    src = input_file(env, p.input)
    output_file(env, src, p.output, "compressed", ".mp4")


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    out = output_file(env, src, p.output, "compressed", ".mp4")
    r = await produce(src, out, ["-i", str(src), "-c:v", "libx264", "-preset", "medium",
                                 "-crf", CRF[p.quality], "-c:a", "aac", "-b:a", "128k",
                                 "-movflags", "+faststart", str(out)], "compressed")
    if r.ok:
        before, after = src.stat().st_size, out.stat().st_size
        r.data["saved_percent"] = round((1 - after / before) * 100, 1) if before else 0
    return r


TOOL = Tool(name="compress", params=P, run=run, action="file.create", target=lambda p: p.input,
            summary=lambda p: f"Compress {p.input} ({p.quality})", precheck=precheck)
