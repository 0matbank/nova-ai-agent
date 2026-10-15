"""Push a `nova/...` branch of a registered project to its remote (RED `git.push`:
the owner approves every push). Never forces; never pushes any other branch.
Verified by the remote branch pointing at the same commit."""

from __future__ import annotations

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.gitops import BRANCH_PREFIX, git, project_folder, remote_url


class P(Params):
    project: str
    branch: str


def precheck(p: P, env: ToolEnv) -> None:
    project_folder(env.config, p.project)
    if not p.branch.startswith(BRANCH_PREFIX) or ".." in p.branch or " " in p.branch:
        raise PolicyDenied(f"only {BRANCH_PREFIX}... branches are pushed, not {p.branch!r}")


async def run(p: P, env: ToolEnv) -> ToolResult:
    _, folder = project_folder(env.config, p.project)
    url = await remote_url(folder)
    if url is None:
        return ToolResult(False, f"{p.project}: no remote to push to", {}, {"pushed": False})
    code, local = await git(folder, "rev-parse", f"refs/heads/{p.branch}")
    if code != 0:
        raise PolicyDenied(f"branch {p.branch} does not exist in {p.project}")
    code, out = await git(folder, "push", "-u", "origin", f"{p.branch}:{p.branch}",
                          timeout=300)
    _, remote = await git(folder, "ls-remote", "origin", f"refs/heads/{p.branch}")
    pushed = code == 0 and remote.split()[:1] == [local.strip()]
    if not pushed:
        return ToolResult(False, f"{p.project}: push failed (network/login?): "
                                 f"{out.strip()[-300:]}", {"output": out[-2000:]},
                          {"pushed": False})
    return ToolResult(True, f"{p.project}: pushed {p.branch} ({local.strip()[:8]})",
                      {"branch": p.branch, "commit": local.strip(), "remote": url},
                      {"pushed": True, "remote_commit": local.strip()})


TOOL = Tool(name="push", params=P, run=run, action="git.push",
            target=lambda p: f"{p.project} → origin/{p.branch}",
            summary=lambda p: f"push {p.branch} of {p.project}", precheck=precheck)
