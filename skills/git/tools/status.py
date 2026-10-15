"""Branch, upstream and changed files of a registered project (GREEN `git.read`)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.gitops import changed_files, current_branch, git, project_folder, remote_url


class P(Params):
    project: str


def precheck(p: P, env: ToolEnv) -> None:
    project_folder(env.config, p.project)


async def run(p: P, env: ToolEnv) -> ToolResult:
    _, folder = project_folder(env.config, p.project)
    branch = await current_branch(folder)
    files = await changed_files(folder)
    _, sb = await git(folder, "status", "-sb", "--porcelain")
    first = sb.splitlines()[0] if sb.strip() else ""
    data = {"branch": branch, "changed": files, "tracking": first.lstrip("# ").strip(),
            "remote": await remote_url(folder)}
    return ToolResult(True, f"{p.project}: on {branch}, {len(files)} changed file(s)", data,
                      {"branch": branch, "changed_count": len(files)})


TOOL = Tool(name="status", params=P, run=run, action="git.read",
            target=lambda p: p.project, precheck=precheck)
