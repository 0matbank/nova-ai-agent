"""media.crop (plan §37)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.mediaops import input_file, output_file, produce


class P(Params):
    input: str
    width: int
    height: int
    x: int = 0
    y: int = 0
    output: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    src = input_file(env, p.input)
    output_file(env, src, p.output, "crop", src.suffix)


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    out = output_file(env, src, p.output, "crop", src.suffix)
    return await produce(src, out, ["-i", str(src), "-vf",
                                    f"crop={p.width}:{p.height}:{p.x}:{p.y}", str(out)],
                         "cropped")


TOOL = Tool(name="crop", params=P, run=run, action="file.create", target=lambda p: p.input,
            summary=lambda p: f"Crop {p.input} to {p.width}x{p.height}", precheck=precheck)
