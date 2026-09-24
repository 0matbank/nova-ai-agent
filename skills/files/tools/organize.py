"""Group the files directly inside a folder into sub-folders by type.
dry_run=true (default) only returns the plan (GREEN). Applying it is
file.organize (YELLOW): allowed only if no move would overwrite anything."""

from __future__ import annotations

import shutil
from pathlib import Path

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult

CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts"},
    "Audio": {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".ppt", ".pptx"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Installers": {".exe", ".msi", ".apk"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml", ".java", ".kt"},
}


class P(Params):
    path: str
    dry_run: bool = True


def plan_moves(folder: Path) -> list[tuple[Path, Path]]:
    moves = []
    for f in sorted(folder.iterdir()):
        if not f.is_file():
            continue
        cat = next((c for c, exts in CATEGORIES.items() if f.suffix.lower() in exts), None)
        if cat is not None:
            moves.append((f, folder / cat / f.name))
    return moves


def precheck(p: P, env: ToolEnv) -> None:
    folder = env.paths.resolve(p.path)
    env.paths.check_write(folder)
    if not folder.is_dir():
        raise PolicyDenied(f"{folder} is not a folder")


def action(p: P, env: ToolEnv) -> str:
    return "search" if p.dry_run else "file.organize"


async def safety_check(p: P, env: ToolEnv) -> tuple[bool, str]:
    moves = plan_moves(env.paths.resolve(p.path))
    clashes = [str(d) for _, d in moves if d.exists()]
    if clashes:
        return False, f"{len(clashes)} destination(s) already exist, e.g. {clashes[0]}"
    return True, f"{len(moves)} moves, no collisions"


async def run(p: P, env: ToolEnv) -> ToolResult:
    folder = env.paths.resolve(p.path)
    moves = plan_moves(folder)
    plan = [{"from": s.name, "to": str(d.relative_to(folder))} for s, d in moves]
    if p.dry_run:
        return ToolResult(True, f"dry run: {len(moves)} file(s) would be organized",
                          {"plan": plan, "applied": False})
    for src, dst in moves:
        dst.parent.mkdir(exist_ok=True)
        shutil.move(str(src), str(dst))
    return ToolResult(True, f"organized {len(moves)} file(s) in {folder}",
                      {"plan": plan, "applied": True},
                      {"all_moved": all(d.exists() and not s.exists() for s, d in moves)})


TOOL = Tool(name="organize", params=P, run=run, action=action, target=lambda p: p.path,
            summary=lambda p: "dry run" if p.dry_run else "apply organize plan",
            safety_check=safety_check, precheck=precheck)
