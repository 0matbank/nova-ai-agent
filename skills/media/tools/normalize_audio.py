"""media.normalize_audio (plan §37)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.mediaops import input_file, output_file, produce


class P(Params):
    input: str
    output: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    src = input_file(env, p.input)
    output_file(env, src, p.output, "normalized", src.suffix)


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    out = output_file(env, src, p.output, "normalized", src.suffix)
    # EBU R128 loudness (-16 LUFS), the video stream is copied as it is
    return await produce(src, out, ["-i", str(src), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                                    "-c:v", "copy", str(out)], "audio normalized")


TOOL = Tool(name="normalize_audio", params=P, run=run, action="file.create",
            target=lambda p: p.input, summary=lambda p: f"Normalize the loudness of {p.input}",
            precheck=precheck)
