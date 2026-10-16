"""Pack files / folders into a new .zip."""

from __future__ import annotations

import zipfile
from pathlib import Path

from pydantic import Field

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult


class P(Params):
    sources: list[str] = Field(min_length=1, max_length=200)
    output: str


def _new_file(env: ToolEnv, raw: str, exts: tuple[str, ...]) -> Path:
    out = env.paths.resolve(raw)
    env.paths.check_write(out)
    if out.suffix.lower() not in exts:
        raise PolicyDenied(f"the file name must end with one of {', '.join(exts)}")
    if out.exists():
        raise PolicyDenied(f"{out} already exists — choose another name")
    return out


def _sources(p: P, env: ToolEnv) -> list[Path]:
    out = []
    for raw in p.sources:
        src = env.paths.resolve(raw)
        env.paths.check_read(src)
        if not src.exists():
            raise PolicyDenied(f"{src} does not exist")
        out.append(src)
    return out


def precheck(p: P, env: ToolEnv) -> None:
    _sources(p, env)
    _new_file(env, p.output, (".zip",))


async def run(p: P, env: ToolEnv) -> ToolResult:
    import asyncio
    srcs = _sources(p, env)
    out = _new_file(env, p.output, (".zip",))
    out.parent.mkdir(parents=True, exist_ok=True)

    def pack() -> int:
        n = 0
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for src in srcs:
                items = [src] if src.is_file() else [f for f in src.rglob("*") if f.is_file()]
                for f in items:
                    if env.paths.skip_during_scan(f):     # never Nova's secrets / DB
                        continue
                    z.write(f, f.relative_to(src.parent))
                    n += 1
        return n
    n = await asyncio.to_thread(pack)
    with zipfile.ZipFile(out) as z:
        bad = z.testzip()
    return ToolResult(bad is None, f"packed {n} file(s) into {out}"
                      + (f" — damaged entry {bad}" if bad else ""),
                      {"output": str(out), "files": n},
                      {"output": str(out), "exists": True, "files": n, "valid": bad is None})


TOOL = Tool(name="zip", params=P, run=run, action="file.create", target=lambda p: p.output,
            summary=lambda p: f"Zip {len(p.sources)} item(s) into {p.output}",
            precheck=precheck)
