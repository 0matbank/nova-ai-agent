"""winget install/uninstall through the broker (RED, machine-wide).

Before asking the owner, Nova looks the exact id up with the normal (non-elevated)
winget so the approval shows the real package name and publisher; an unknown id is
refused without asking."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Literal

from pydantic import Field

from core.broker.skill import run_on_broker
from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult

_INFO: dict[str, str] = {}


class P(Params):
    op: Literal["install", "uninstall"] = "install"
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.+_-]{1,99}$",
                    description="exact winget id, e.g. 7zip.7zip")


def action(p: P, env: ToolEnv) -> str:
    return "admin.package_install" if p.op == "install" else "admin.package_uninstall"


def lookup(package_id: str) -> str | None:
    """'Name — Publisher (version)' from `winget show`, None if winget has no such id."""
    exe = shutil.which("winget")
    if exe is None:
        return ""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run([exe, "show", "--id", package_id,  # noqa: S603 - validated id
                            "--exact", "--source", "winget",
                            "--accept-source-agreements", "--disable-interactivity"],
                           capture_output=True, timeout=60, creationflags=flags, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0:
        return None
    fields = {}
    for line in out.splitlines():
        m = re.match(r"^\s*(Version|Publisher):\s*(.+)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    m = re.search(r"Found (.+?) \[", out)
    name = m.group(1).strip() if m else package_id
    return f"{name} — {fields.get('Publisher', '?')} ({fields.get('Version', '?')})"


def precheck(p: P, env: ToolEnv) -> None:
    if p.op != "install" or p.id in _INFO:
        return
    info = lookup(p.id)
    if info is None:
        raise PolicyDenied(f"winget-এ '{p.id}' নামে কোনো package নেই (exact id লাগবে)")
    _INFO[p.id] = info


def _summary(p: P) -> str:
    info = _INFO.get(p.id) or p.id
    verb = "install (সব user-এর জন্য, administrator)" if p.op == "install" else "uninstall"
    return f"winget {verb}: {info}"


async def run(p: P, env: ToolEnv) -> ToolResult:
    return await run_on_broker("package", p, f"package {p.id}", env)


TOOL = Tool(name="package", params=P, run=run, action=action,
            target=lambda p: f"package {p.id}", summary=_summary, precheck=precheck)
