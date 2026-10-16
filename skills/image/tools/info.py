"""Size, format and dimensions of an image (read-only)."""

from __future__ import annotations

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult


class P(Params):
    input: str


def precheck(p: P, env: ToolEnv) -> None:
    path = env.paths.resolve(p.input)
    env.paths.check_read(path)
    if not path.is_file():
        raise PolicyDenied(f"{path} is not a file")


async def run(p: P, env: ToolEnv) -> ToolResult:
    from PIL import Image
    path = env.paths.resolve(p.input)
    try:
        with Image.open(path) as im:
            data = {"format": im.format, "width": im.width, "height": im.height,
                    "mode": im.mode, "size": path.stat().st_size}
    except OSError as e:
        return ToolResult(False, f"{path.name}: not an image ({e})", {}, {"readable": False})
    return ToolResult(True, f"{path.name}: {data['width']}x{data['height']} {data['format']}",
                      data, {"readable": True})


TOOL = Tool(name="info", params=P, run=run, action="file.read", target=lambda p: p.input,
            precheck=precheck)
