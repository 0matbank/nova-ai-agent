"""Convert to another format — the container picks sensible codecs."""

from __future__ import annotations

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.mediaops import input_file, output_file, produce

FORMATS = ("mp4", "mkv", "mov", "webm", "avi", "mp3", "wav", "m4a", "aac", "flac", "ogg", "gif")


class P(Params):
    input: str
    to: str                           # mp4, mp3, wav, gif, …
    output: str | None = None


def _ext(p: P) -> str:
    to = p.to.lower().lstrip(".")
    if to not in FORMATS:
        raise PolicyDenied(f"format must be one of {', '.join(FORMATS)}")
    return "." + to


def precheck(p: P, env: ToolEnv) -> None:
    src = input_file(env, p.input)
    output_file(env, src, p.output, "converted", _ext(p))


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    ext = _ext(p)
    out = output_file(env, src, p.output, "converted", ext)
    extra = ["-vf", "fps=10,scale=480:-2:flags=lanczos"] if ext == ".gif" else []
    if ext in (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"):
        extra = ["-vn"]
    return await produce(src, out, ["-i", str(src), *extra, str(out)], f"converted to {ext}")


TOOL = Tool(name="convert", params=P, run=run, action="file.create", target=lambda p: p.input,
            summary=lambda p: f"Convert {p.input} to {p.to}", precheck=precheck)
