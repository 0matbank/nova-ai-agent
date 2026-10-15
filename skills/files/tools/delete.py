"""Delete (plan §34: important/path-wide delete needs approval; §45: no silent
destructive delete).

- a file in the agent workspace       → file.delete_workspace (BLUE)
- a file anywhere else, or inside a
  registered project folder           → file.delete_important (RED)
- any folder, inside the workspace too → file.delete_pathwide (RED)
Everything goes to the Recycle Bin — nothing is deleted permanently (audit 2026-10-15:
a permanent BLUE folder delete in the workspace could wipe a project, and after a
BLUE move of a user folder into the workspace it bypassed the RED rule).
"""

from __future__ import annotations

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
    if path.is_dir():
        return "file.delete_pathwide"
    if env.paths.in_workspace(path) and not env.paths.in_project(path):
        return "file.delete_workspace"
    return "file.delete_important"


async def run(p: P, env: ToolEnv) -> ToolResult:
    path = env.paths.resolve(p.path)
    send2trash(str(path))
    how = "moved to Recycle Bin"
    return ToolResult(True, f"{how}: {path}", {"path": str(path), "how": how},
                      {"exists": path.exists()})


TOOL = Tool(name="delete", params=P, run=run, action=action, target=lambda p: p.path,
            summary=lambda p: "delete", precheck=precheck)
