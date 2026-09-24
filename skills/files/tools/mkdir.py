from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult


class P(Params):
    path: str


def precheck(p: P, env: ToolEnv) -> None:
    env.paths.check_write(env.paths.resolve(p.path))


async def run(p: P, env: ToolEnv) -> ToolResult:
    path = env.paths.resolve(p.path)
    existed = path.is_dir()
    path.mkdir(parents=True, exist_ok=True)
    return ToolResult(True, f"folder {'exists' if existed else 'created'}: {path}",
                      {"path": str(path), "created": not existed}, {"is_dir": path.is_dir()})


TOOL = Tool(name="mkdir", params=P, run=run, action="file.create",
            target=lambda p: p.path, precheck=precheck)
