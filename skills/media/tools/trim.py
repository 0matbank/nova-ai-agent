"""media.trim (plan §37)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.mediaops import check_time, input_file, output_file, produce


class P(Params):
    input: str
    start: str = "0"                  # seconds or 1:30 / 00:01:30.5
    end: str | None = None
    duration: str | None = None
    output: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    src = input_file(env, p.input)
    for name in ("start", "end", "duration"):
        check_time(getattr(p, name), name)
    output_file(env, src, p.output, "trim", src.suffix)


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    out = output_file(env, src, p.output, "trim", src.suffix)
    args = ["-i", str(src), "-ss", p.start]
    if p.end:
        args += ["-to", p.end]
    elif p.duration:
        args += ["-t", p.duration]
    return await produce(src, out, [*args, str(out)], "trimmed")


TOOL = Tool(name="trim", params=P, run=run, action="file.create", target=lambda p: p.input,
            summary=lambda p: f"Trim {p.input} from {p.start}"
                              + (f" to {p.end}" if p.end else ""), precheck=precheck)
