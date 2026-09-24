"""Create a file (BLUE) or overwrite/append to an existing one (YELLOW: a
backup is made first; if the backup fails the change needs approval)."""

from __future__ import annotations

from typing import Literal

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.fileops import backup_file, sha256


class P(Params):
    path: str
    content: str
    mode: Literal["create", "overwrite", "append"] = "create"
    encoding: str = "utf-8"


def precheck(p: P, env: ToolEnv) -> None:
    path = env.paths.resolve(p.path)
    env.paths.check_write(path)
    if path.is_dir():
        raise PolicyDenied(f"{path} is a folder")
    if p.mode == "create" and path.exists():
        raise PolicyDenied(f"{path} already exists — use mode=overwrite or append")
    if p.mode != "create" and not path.exists():
        raise PolicyDenied(f"{path} does not exist — use mode=create")


def action(p: P, env: ToolEnv) -> str:
    return "file.create" if p.mode == "create" else "file.overwrite"


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    try:
        b = backup_file(env.paths.resolve(p.path), env.paths.backups)
    except OSError as e:
        return False, f"backup failed: {e}"
    return True, f"backup at {b}"


async def run(p: P, env: ToolEnv) -> ToolResult:
    path = env.paths.resolve(p.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if p.mode == "append":
        with path.open("a", encoding=p.encoding, newline="") as fh:
            fh.write(p.content)
    else:
        path.write_text(p.content, encoding=p.encoding, newline="")
    return ToolResult(True, f"{p.mode}: {path} ({path.stat().st_size} bytes)",
                      {"path": str(path)},
                      {"exists": path.exists(), "size": path.stat().st_size,
                       "sha256": sha256(path)})


TOOL = Tool(name="write", params=P, run=run, action=action, target=lambda p: p.path,
            summary=lambda p: f"{p.mode} {len(p.content)} chars",
            safety_check=safety_check, precheck=precheck)
