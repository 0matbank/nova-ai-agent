"""Resize / convert / compress an image into a NEW file (Pillow)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult

FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP", ".bmp": "BMP",
           ".gif": "GIF", ".tif": "TIFF", ".tiff": "TIFF"}


class P(Params):
    input: str
    width: int | None = Field(default=None, ge=1, le=20_000)    # aspect kept if one is given
    height: int | None = Field(default=None, ge=1, le=20_000)
    to: str | None = None             # jpg png webp …; default: the same format
    quality: Literal["high", "medium", "small"] = "high"
    output: str | None = None


def _paths(p: P, env: ToolEnv) -> tuple[Path, Path]:
    src = env.paths.resolve(p.input)
    env.paths.check_read(src)
    if not src.is_file():
        raise PolicyDenied(f"{src} is not a file")
    ext = ("." + p.to.lower().lstrip(".")) if p.to else src.suffix.lower()
    if ext not in FORMATS:
        raise PolicyDenied(f"format must be one of {', '.join(FORMATS)}")
    if p.output:
        out = env.paths.resolve(p.output)
    else:
        out, n = src.with_name(f"{src.stem}_edited{ext}"), 2
        while out.exists():
            out, n = src.with_name(f"{src.stem}_edited_{n}{ext}"), n + 1
    env.paths.check_write(out)
    if out.exists():
        raise PolicyDenied(f"{out} already exists — choose another name")
    return src, out


def precheck(p: P, env: ToolEnv) -> None:
    _paths(p, env)


async def run(p: P, env: ToolEnv) -> ToolResult:
    from PIL import Image
    src, out = _paths(p, env)
    with Image.open(src) as im:
        img = im.copy()
    if p.width or p.height:
        w = p.width or round(img.width * (p.height or img.height) / img.height)
        h = p.height or round(img.height * w / img.width)
        img = img.resize((w, h), Image.Resampling.LANCZOS)
    fmt = FORMATS[out.suffix.lower()]
    if fmt == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    q = {"high": 92, "medium": 80, "small": 65}[p.quality]
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, fmt, **({"quality": q, "optimize": True} if fmt in ("JPEG", "WEBP")
                          else {"optimize": True} if fmt == "PNG" else {}))
    with Image.open(out) as check:
        dims = f"{check.width}x{check.height}"
    return ToolResult(True, f"{out} ({dims}, {out.stat().st_size / 1024:.0f} KB)",
                      {"input": str(src), "output": str(out)},
                      {"output": str(out), "exists": True, "size": out.stat().st_size})


TOOL = Tool(name="process", params=P, run=run, action="file.create", target=lambda p: p.input,
            summary=lambda p: f"Resize/convert {p.input}", precheck=precheck)
