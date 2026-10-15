"""Commit the pending change of a registered project on its own `nova/...` branch
(YELLOW `git.commit`). Safety check: nothing in the change looks like a secret
(owner rule: never commit a token, key or .env). The project's original branch
is restored afterwards, so the owner's checkout is left as it was."""

from __future__ import annotations

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.gitops import (
    BRANCH_PREFIX,
    changed_files,
    current_branch,
    git,
    head,
    project_folder,
    secret_findings,
)


class P(Params):
    project: str
    message: str
    branch: str                       # must start with nova/


def precheck(p: P, env: ToolEnv) -> None:
    project_folder(env.config, p.project)
    if not p.branch.startswith(BRANCH_PREFIX) or ".." in p.branch or " " in p.branch:
        raise PolicyDenied(f"commits go to a {BRANCH_PREFIX}... branch only, not {p.branch!r}")
    if not p.message.strip():
        raise PolicyDenied("empty commit message")


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    _, folder = project_folder(env.config, p.project)
    hits = await secret_findings(folder)
    if hits:
        return False, f"possible secret in the change: {', '.join(hits[:5])}"
    return True, "no secrets found in the change"


async def run(p: P, env: ToolEnv) -> ToolResult:
    _, folder = project_folder(env.config, p.project)
    files = await changed_files(folder)
    if not files:
        return ToolResult(False, f"{p.project}: nothing to commit", {}, {"committed": False})
    original = await current_branch(folder)
    steps = [("switch", "-c", p.branch)] if original != p.branch else []
    steps += [("add", "-A", "--", ".", ":(exclude)**/__pycache__/**"),
              ("commit", "-q", "-m", p.message.strip())]
    for args in steps:
        code, out = await git(folder, *args)
        if code != 0:
            if original and original != p.branch:
                await git(folder, "switch", original)
            return ToolResult(False, f"{p.project}: git {args[0]} failed: {out.strip()[-300:]}",
                              {"output": out[-2000:]}, {"committed": False})
    sha = await head(folder)
    _, stat = await git(folder, "show", "--stat", "--format=", sha)
    if original and original != p.branch:
        await git(folder, "switch", original)            # the owner's checkout as it was
    return ToolResult(True, f"{p.project}: committed {sha[:8]} on {p.branch}",
                      {"commit": sha, "branch": p.branch, "original_branch": original,
                       "files": files, "stat": stat.strip()},
                      {"committed": True, "commit": sha, "branch": p.branch})


TOOL = Tool(name="commit", params=P, run=run, action="git.commit",
            target=lambda p: f"{p.project} → {p.branch}",
            summary=lambda p: f"commit on {p.branch}: {p.message[:200]}",
            safety_check=safety_check, precheck=precheck)
