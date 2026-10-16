"""What is inside a .zip (read-only)."""

from __future__ import annotations

import zipfile

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult


class P(Params):
    archive: str


def precheck(p: P, env: ToolEnv) -> None:
    path = env.paths.resolve(p.archive)
    env.paths.check_read(path)
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise PolicyDenied(f"{path} is not a zip file")


async def run(p: P, env: ToolEnv) -> ToolResult:
    path = env.paths.resolve(p.archive)
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
    files = [{"name": i.filename, "size": i.file_size} for i in infos if not i.is_dir()]
    total = sum(int(f["size"]) for f in files)
    return ToolResult(True, f"{path.name}: {len(files)} file(s), {total / 1024 / 1024:.1f} MB "
                            "unpacked", {"files": files[:500], "total_size": total},
                      {"files": len(files)}, untrusted=True)


TOOL = Tool(name="list", params=P, run=run, action="file.read", target=lambda p: p.archive,
            precheck=precheck)
