from __future__ import annotations

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.fileops import iso


class P(Params):
    path: str
    limit: int = 200


def precheck(p: P, env: ToolEnv) -> None:
    path = env.paths.resolve(p.path)
    env.paths.check_read(path)
    if not path.is_dir():
        raise PolicyDenied(f"{path} is not a folder")


async def run(p: P, env: ToolEnv) -> ToolResult:
    path = env.paths.resolve(p.path)
    entries = []
    for child in sorted(path.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if env.paths.skip_during_scan(child):
            continue
        st = child.stat()
        entries.append({"name": child.name, "dir": child.is_dir(),
                        "size": None if child.is_dir() else st.st_size,
                        "modified": iso(st.st_mtime)})
        if len(entries) >= p.limit:
            break
    return ToolResult(True, f"{len(entries)} entries in {path}",
                      {"path": str(path), "entries": entries}, {"exists": True})


TOOL = Tool(name="list_dir", params=P, run=run, action="file.read",
            target=lambda p: p.path, precheck=precheck)
