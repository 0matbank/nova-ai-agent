"""Remove a part (start → end) from the middle of a video / audio; the rest is kept."""

from __future__ import annotations

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.mediaops import check_time, input_file, output_file, probe, produce, seconds


class P(Params):
    input: str
    start: str
    end: str
    output: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    src = input_file(env, p.input)
    check_time(p.start, "start")
    check_time(p.end, "end")
    if seconds(p.end) <= seconds(p.start):
        raise PolicyDenied("end must be after start")
    output_file(env, src, p.output, "cut", src.suffix)


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    out = output_file(env, src, p.output, "cut", src.suffix)
    info = await probe(src)
    s, e = seconds(p.start), seconds(p.end)
    parts, maps = [], []
    if "video" in info:
        parts += [f"[0:v]trim=0:{s},setpts=PTS-STARTPTS[v0]",
                  f"[0:v]trim=start={e},setpts=PTS-STARTPTS[v1]"]
    if "audio" in info:
        parts += [f"[0:a]atrim=0:{s},asetpts=PTS-STARTPTS[a0]",
                  f"[0:a]atrim=start={e},asetpts=PTS-STARTPTS[a1]"]
    v, a = int("video" in info), int("audio" in info)
    pairs = "".join(f"{'[v0]' if v else ''}{'[a0]' if a else ''}"
                    f"{'[v1]' if v else ''}{'[a1]' if a else ''}")
    outs = ("[v]" if v else "") + ("[a]" if a else "")
    graph = ";".join([*parts, f"{pairs}concat=n=2:v={v}:a={a}{outs}"])
    for label in ("[v]", "[a]"):
        if label in outs:
            maps += ["-map", label]
    return await produce(src, out, ["-i", str(src), "-filter_complex", graph, *maps, str(out)],
                         f"cut {p.start}–{p.end} out")


TOOL = Tool(name="cut", params=P, run=run, action="file.create", target=lambda p: p.input,
            summary=lambda p: f"Cut {p.start}–{p.end} out of {p.input}", precheck=precheck)
