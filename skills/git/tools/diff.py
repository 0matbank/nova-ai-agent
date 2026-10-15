"""The pending change of a registered project: stat + patch (GREEN `git.read`)."""

from __future__ import annotations

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.gitops import git, project_folder

MAX_PATCH = 20_000


class P(Params):
    project: str
    ref: str = "HEAD"        # compare the working tree with this commit / branch


def precheck(p: P, env: ToolEnv) -> None:
    project_folder(env.config, p.project)


async def run(p: P, env: ToolEnv) -> ToolResult:
    _, folder = project_folder(env.config, p.project)
    _, stat = await git(folder, "diff", "--stat", p.ref, "--")
    _, patch = await git(folder, "diff", p.ref, "--")
    cut = len(patch) > MAX_PATCH
    summary = stat.strip().splitlines()[-1].strip() if stat.strip() else "no changes"
    return ToolResult(True, f"{p.project}: {summary}",
                      {"stat": stat.strip(), "patch": patch[:MAX_PATCH], "truncated": cut},
                      {"empty": not patch.strip()})


TOOL = Tool(name="diff", params=P, run=run, action="git.read",
            target=lambda p: f"{p.project}@{p.ref}", precheck=precheck)
