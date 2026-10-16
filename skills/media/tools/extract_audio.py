"""media.extract_audio (plan §37)."""

from __future__ import annotations

from typing import Literal

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.mediaops import input_file, output_file, produce


class P(Params):
    input: str
    format: Literal["mp3", "wav", "m4a"] = "mp3"
    output: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    src = input_file(env, p.input)
    output_file(env, src, p.output, "audio", "." + p.format)


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    out = output_file(env, src, p.output, "audio", "." + p.format)
    return await produce(src, out, ["-i", str(src), "-vn", str(out)], "audio extracted")


TOOL = Tool(name="extract_audio", params=P, run=run, action="file.create",
            target=lambda p: p.input, summary=lambda p: f"Extract the audio of {p.input}",
            precheck=precheck)
