"""media.subtitle_burn (plan §37)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.mediaops import input_file, output_file, produce


class P(Params):
    input: str
    subtitles: str                    # .srt / .ass file
    output: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    src = input_file(env, p.input)
    input_file(env, p.subtitles)
    output_file(env, src, p.output, "subtitled", ".mp4")


def _filter_path(path: str) -> str:
    # ffmpeg filter syntax: forward slashes, the drive colon escaped
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    subs = input_file(env, p.subtitles)
    out = output_file(env, src, p.output, "subtitled", ".mp4")
    return await produce(src, out, ["-i", str(src), "-vf",
                                    f"subtitles='{_filter_path(str(subs))}'", str(out)],
                         "subtitles burned in")


TOOL = Tool(name="subtitle_burn", params=P, run=run, action="file.create",
            target=lambda p: p.input, summary=lambda p: f"Burn {p.subtitles} into {p.input}",
            precheck=precheck)
