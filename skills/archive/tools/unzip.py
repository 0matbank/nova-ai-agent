"""Unpack a .zip into a NEW folder — safely: no path escapes (zip-slip), size and
file-count limits (zip bombs), never over existing files."""

from __future__ import annotations

import zipfile
from pathlib import Path

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult

MAX_TOTAL = 4 * 1024 ** 3
MAX_FILES = 20_000
MAX_RATIO = 200                       # unpacked / packed — more looks like a zip bomb


class P(Params):
    archive: str
    destination: str | None = None    # default: a new folder next to the zip


def _paths(p: P, env: ToolEnv) -> tuple[Path, Path]:
    src = env.paths.resolve(p.archive)
    env.paths.check_read(src)
    if not src.is_file() or not zipfile.is_zipfile(src):
        raise PolicyDenied(f"{src} is not a zip file")
    dst = env.paths.resolve(p.destination) if p.destination else src.with_suffix("")
    env.paths.check_write(dst)
    if dst.exists() and any(dst.iterdir()):
        raise PolicyDenied(f"{dst} already has files — choose a new folder")
    with zipfile.ZipFile(src) as z:
        infos = z.infolist()
    total = sum(i.file_size for i in infos)
    packed = max(1, src.stat().st_size)
    if len(infos) > MAX_FILES or total > MAX_TOTAL or total / packed > MAX_RATIO:
        raise PolicyDenied(f"refused: {len(infos)} files, {total / 1024 ** 3:.1f} GB unpacked "
                           "— too big or looks like a zip bomb")
    root = dst.resolve()
    for i in infos:
        target = (dst / i.filename).resolve()
        if target != root and root not in target.parents:
            raise PolicyDenied(f"refused: entry {i.filename!r} would land outside {dst}")
    return src, dst


def precheck(p: P, env: ToolEnv) -> None:
    _paths(p, env)


async def run(p: P, env: ToolEnv) -> ToolResult:
    import asyncio
    src, dst = _paths(p, env)
    dst.mkdir(parents=True, exist_ok=True)

    def unpack() -> int:
        with zipfile.ZipFile(src) as z:
            z.extractall(dst)
            return len([i for i in z.infolist() if not i.is_dir()])
    n = await asyncio.to_thread(unpack)
    found = sum(1 for f in dst.rglob("*") if f.is_file())
    return ToolResult(found >= n, f"unpacked {n} file(s) into {dst}",
                      {"output": str(dst), "files": n},
                      {"output": str(dst), "exists": True, "files": found})


TOOL = Tool(name="unzip", params=P, run=run, action="file.create", target=lambda p: p.archive,
            summary=lambda p: f"Unzip {p.archive}", precheck=precheck)
