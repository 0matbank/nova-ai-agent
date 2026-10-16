"""media.thumbnail (plan §37)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.mediaops import check_time, input_file, output_file, produce


class P(Params):
    input: str
    at: str = "1"                     # seconds or 0:01:05
    output: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    src = input_file(env, p.input)
    check_time(p.at, "at")
    output_file(env, src, p.output, "thumb", ".jpg")


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    out = output_file(env, src, p.output, "thumb", ".jpg")
    return await produce(src, out, ["-ss", p.at, "-i", str(src), "-frames:v", "1",
                                    "-q:v", "2", str(out)], "thumbnail")


TOOL = Tool(name="thumbnail", params=P, run=run, action="file.create",
            target=lambda p: p.input, summary=lambda p: f"Thumbnail of {p.input} at {p.at}",
            precheck=precheck)
