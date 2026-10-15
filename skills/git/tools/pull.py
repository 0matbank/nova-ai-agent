"""Bring a registered project up to date (BLUE `git.pull`): fast-forward only, on a
clean tree — it can never merge, rebase or overwrite local work."""

from __future__ import annotations

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.gitops import changed_files, git, head, project_folder, remote_url


class P(Params):
    project: str


def precheck(p: P, env: ToolEnv) -> None:
    project_folder(env.config, p.project)


async def run(p: P, env: ToolEnv) -> ToolResult:
    _, folder = project_folder(env.config, p.project)
    if await remote_url(folder) is None:
        return ToolResult(True, f"{p.project}: no remote — nothing to pull",
                          {"pulled": False}, {"remote": None})
    if await changed_files(folder):
        raise PolicyDenied(f"{p.project} has uncommitted changes — not pulling over them")
    before = await head(folder)
    code, out = await git(folder, "pull", "--ff-only", "--no-rebase", timeout=300)
    after = await head(folder)
    if code != 0:
        return ToolResult(False, f"{p.project}: pull failed (not a fast-forward, or no "
                                 f"network/login): {out.strip()[-300:]}", {"output": out[-2000:]},
                          {"head": after})
    return ToolResult(True, f"{p.project}: "
                            + ("already up to date" if before == after else
                               f"updated {before[:8]} → {after[:8]}"),
                      {"pulled": before != after, "output": out[-2000:]},
                      {"before": before, "head": after})


TOOL = Tool(name="pull", params=P, run=run, action="git.pull",
            target=lambda p: p.project, precheck=precheck)
