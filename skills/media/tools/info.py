"""Duration, streams, resolution, bitrate of a video / audio file (read-only)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.mediaops import input_file, probe


class P(Params):
    input: str


def precheck(p: P, env: ToolEnv) -> None:
    input_file(env, p.input)


async def run(p: P, env: ToolEnv) -> ToolResult:
    src = input_file(env, p.input)
    info = await probe(src)
    if "error" in info:
        return ToolResult(False, f"{src.name}: {info['error']}", info, {"readable": False})
    parts = [f"{info['duration_seconds']}s" if "duration_seconds" in info else ""]
    if "video" in info:
        v = info["video"]
        parts.append(f"{v['width']}x{v['height']} {v['codec']}"
                     + (f" {v['fps']}fps" if v.get("fps") else ""))
    if "audio" in info:
        parts.append(f"audio {info['audio']['codec']}")
    return ToolResult(True, f"{src.name}: " + ", ".join(x for x in parts if x), info,
                      {"readable": True})


TOOL = Tool(name="info", params=P, run=run, action="file.read", target=lambda p: p.input,
            precheck=precheck)
