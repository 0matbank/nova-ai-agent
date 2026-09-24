from __future__ import annotations

import asyncio

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.fileops import iso, sha256


class P(Params):
    path: str
    hash: bool = True


def precheck(p: P, env: ToolEnv) -> None:
    path = env.paths.resolve(p.path)
    env.paths.check_read(path)
    if not path.exists():
        raise PolicyDenied(f"{path} does not exist")


def _run(p: P, env: ToolEnv) -> ToolResult:
    path = env.paths.resolve(p.path)
    st = path.stat()
    data = {
        "path": str(path), "dir": path.is_dir(), "size": st.st_size,
        "created": iso(st.st_ctime), "modified": iso(st.st_mtime),
        "accessed": iso(st.st_atime), "suffix": path.suffix.lower(),
        "sha256": sha256(path) if p.hash and path.is_file() else None,
    }
    return ToolResult(True, f"{path.name}: {st.st_size} bytes, modified {data['modified']}",
                      data, {"exists": True})


async def run(p: P, env: ToolEnv) -> ToolResult:
    return await asyncio.to_thread(_run, p, env)   # disk-heavy: keep the loop free


TOOL = Tool(name="metadata", params=P, run=run, action="file.read",
            target=lambda p: p.path, precheck=precheck)
