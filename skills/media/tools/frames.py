"""Capture frames every N seconds into a new folder (frame capture)."""

from __future__ import annotations

from pydantic import Field

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.mediaops import input_file, run_ffmpeg


class P(Params):
    input: str
    every_seconds: float = Field(default=10, gt=0, le=3600)
    max_frames: int = Field(default=20, ge=1, le=200)
    output_dir: str | None = None


def _out(p: P, env: ToolEnv):  # type: ignore[no-untyped-def]
    src = input_file(env, p.input)
    out = env.paths.resolve(p.output_dir) if p.output_dir else \
        src.with_name(f"{src.stem}_frames")
    env.paths.check_write(out)
    if out.exists() and any(out.iterdir()):
        raise PolicyDenied(f"{out} already has files — choose an empty or new folder")
    return src, out


def precheck(p: P, env: ToolEnv) -> None:
    _out(p, env)


async def run(p: P, env: ToolEnv) -> ToolResult:
    src, out = _out(p, env)
    out.mkdir(parents=True, exist_ok=True)
    ok, err = await run_ffmpeg(["-i", str(src), "-vf", f"fps=1/{p.every_seconds}",
                                "-frames:v", str(p.max_frames), "-q:v", "3",
                                str(out / f"{src.stem}_%03d.jpg")])
    files = sorted(out.glob("*.jpg"))
    if not ok or not files:
        return ToolResult(False, f"frame capture failed: {err.strip()[-300:]}",
                          {"input": str(src)}, {"exists": False})
    return ToolResult(True, f"{len(files)} frame(s) in {out}",
                      {"output_dir": str(out), "frames": [f.name for f in files]},
                      {"output": str(out), "exists": True, "count": len(files)})


TOOL = Tool(name="frames", params=P, run=run, action="file.create", target=lambda p: p.input,
            summary=lambda p: f"Frames of {p.input} every {p.every_seconds}s",
            precheck=precheck)
