"""Write rows into a new Excel sheet (.xlsx) or CSV — the first row is the header."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from pydantic import Field

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.docops import write_sheet


class P(Params):
    path: str
    rows: list[list[Any]] = Field(min_length=1, max_length=50_000)
    sheet: str = "Sheet1"


def _new_file(env: ToolEnv, raw: str, exts: tuple[str, ...]) -> Path:
    out = env.paths.resolve(raw)
    env.paths.check_write(out)
    if out.suffix.lower() not in exts:
        raise PolicyDenied(f"the file name must end with one of {', '.join(exts)}")
    if out.exists():
        raise PolicyDenied(f"{out} already exists — choose another name")
    return out


def precheck(p: P, env: ToolEnv) -> None:
    _new_file(env, p.path, (".xlsx", ".csv"))


async def run(p: P, env: ToolEnv) -> ToolResult:
    out = _new_file(env, p.path, (".xlsx", ".csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".csv":
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(p.rows)
        n = len(p.rows)
    else:
        n = write_sheet(out, p.rows, p.sheet)
    return ToolResult(True, f"created {out} ({n} rows)", {"output": str(out), "rows": n},
                      {"output": str(out), "exists": out.is_file(), "rows": n})


TOOL = Tool(name="sheet_create", params=P, run=run, action="file.create",
            target=lambda p: p.path, summary=lambda p: f"Create the sheet {p.path}",
            precheck=precheck)
