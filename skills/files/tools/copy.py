from __future__ import annotations

import shutil

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.fileops import backup_file


class P(Params):
    source: str
    destination: str
    overwrite: bool = False


def precheck(p: P, env: ToolEnv) -> None:
    src, dst = env.paths.resolve(p.source), env.paths.resolve(p.destination)
    env.paths.check_read(src)
    env.paths.check_write(dst)
    if not src.exists():
        raise PolicyDenied(f"{src} does not exist")
    if dst.exists() and not p.overwrite:
        raise PolicyDenied(f"{dst} already exists — set overwrite=true")


def action(p: P, env: ToolEnv) -> str:
    return "file.overwrite" if env.paths.resolve(p.destination).exists() else "file.create"


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    try:
        b = backup_file(env.paths.resolve(p.destination), env.paths.backups)
    except OSError as e:
        return False, f"backup failed: {e}"
    return True, f"backup at {b}"


async def run(p: P, env: ToolEnv) -> ToolResult:
    src, dst = env.paths.resolve(p.source), env.paths.resolve(p.destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=p.overwrite)
    else:
        shutil.copy2(src, dst)
    return ToolResult(True, f"copied {src} → {dst}", {"destination": str(dst)},
                      {"source_exists": src.exists(), "destination_exists": dst.exists()})


TOOL = Tool(name="copy", params=P, run=run, action=action,
            target=lambda p: f"{p.source} -> {p.destination}",
            safety_check=safety_check, precheck=precheck)
