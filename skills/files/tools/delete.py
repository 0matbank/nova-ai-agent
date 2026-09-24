"""Delete (plan §34: important/path-wide delete needs approval; §45: no silent
destructive delete).

- inside the agent workspace      → file.delete_workspace (BLUE), permanent
- a file anywhere else            → file.delete_important (RED), Recycle Bin
- a folder anywhere else          → file.delete_pathwide (RED), Recycle Bin
"""

from __future__ import annotations

import shutil

from send2trash import send2trash

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult


class P(Params):
    path: str


def precheck(p: P, env: ToolEnv) -> None:
    path = env.paths.resolve(p.path)
    env.paths.check_delete(path)
    if not path.exists():
        raise PolicyDenied(f"{path} does not exist")


def action(p: P, env: ToolEnv) -> str:
    path = env.paths.resolve(p.path)
    if env.paths.in_workspace(path):
        return "file.delete_workspace"
    return "file.delete_pathwide" if path.is_dir() else "file.delete_important"


async def run(p: P, env: ToolEnv) -> ToolResult:
    path = env.paths.resolve(p.path)
    if env.paths.in_workspace(path):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        how = "deleted"
    else:
        send2trash(str(path))
        how = "moved to Recycle Bin"
    return ToolResult(True, f"{how}: {path}", {"path": str(path), "how": how},
                      {"exists": path.exists()})


TOOL = Tool(name="delete", params=P, run=run, action=action, target=lambda p: p.path,
            summary=lambda p: "delete", precheck=precheck)
