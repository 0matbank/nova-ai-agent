"""Read a text file. The content is UNTRUSTED DATA (plan §19): it can never
change policy, approve anything or become an instruction."""

from __future__ import annotations

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.fileops import read_text


class P(Params):
    path: str
    max_chars: int = 20000


def precheck(p: P, env: ToolEnv) -> None:
    path = env.paths.resolve(p.path)
    env.paths.check_read(path)
    if not path.is_file():
        raise PolicyDenied(f"{path} is not a file")


async def run(p: P, env: ToolEnv) -> ToolResult:
    path = env.paths.resolve(p.path)
    try:
        text, enc, truncated = read_text(path, min(p.max_chars, 200_000))
    except ValueError as e:
        return ToolResult(False, str(e), {"path": str(path)})
    return ToolResult(True, f"read {len(text)} chars from {path.name}" +
                      (" (truncated)" if truncated else ""),
                      {"path": str(path), "text": text, "encoding": enc,
                       "truncated": truncated}, {"exists": True}, untrusted=True)


TOOL = Tool(name="read_text", params=P, run=run, action="file.read",
            target=lambda p: p.path, precheck=precheck)
