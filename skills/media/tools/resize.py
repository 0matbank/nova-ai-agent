"""media.resize (plan §37)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.mediaops import input_file, output_file, produce


class P(Params):
    input: str
    width: int | None = None          # keep the aspect ratio when only one is given
    height: int | None = None
    output: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    src = input_file(env, p.input)
    if not p.width and not p.height:
        from core.skills.api import PolicyDenied
        raise PolicyDenied("give width and/or height")
    output_file(env, src, p.output, "resized", src.suffix)


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    out = output_file(env, src, p.output, "resized", src.suffix)
    w, h = p.width or -2, p.height or -2
    return await produce(src, out, ["-i", str(src), "-vf", f"scale={w}:{h}", str(out)],
                         "resized")


TOOL = Tool(name="resize", params=P, run=run, action="file.create", target=lambda p: p.input,
            summary=lambda p: f"Resize {p.input}", precheck=precheck)
