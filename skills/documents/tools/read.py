"""Read a PDF / Word / Excel / CSV / text document (text is untrusted data)."""

from __future__ import annotations

from pydantic import Field

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.docops import MAX_READ_CHARS, READABLE, read_document


class P(Params):
    path: str
    max_chars: int = Field(default=20_000, ge=200, le=MAX_READ_CHARS)


def precheck(p: P, env: ToolEnv) -> None:
    path = env.paths.resolve(p.path)
    env.paths.check_read(path)
    if not path.is_file():
        raise PolicyDenied(f"{path} is not a file")
    if path.suffix.lower() not in READABLE:
        raise PolicyDenied(f"can read {', '.join(READABLE)} — not {path.suffix}")


async def run(p: P, env: ToolEnv) -> ToolResult:
    import asyncio
    path = env.paths.resolve(p.path)
    try:
        text, meta = await asyncio.to_thread(read_document, path, p.max_chars)
    except Exception as e:  # noqa: BLE001 - a broken file is a result, not a crash
        return ToolResult(False, f"{path.name}: could not read ({type(e).__name__}: {e})",
                          {"path": str(path)}, {"readable": False})
    what = ", ".join(f"{k} {v}" for k, v in meta.items()
                     if k in ("pages", "paragraphs", "tables", "rows") and v)
    return ToolResult(True, f"{path.name}: {meta['chars']} characters" + (f" ({what})"
                                                                        if what else ""),
                      {"path": str(path), "meta": meta, "text": text},
                      {"readable": True, "chars": meta["chars"]}, untrusted=True)


TOOL = Tool(name="read", params=P, run=run, action="file.read", target=lambda p: p.path,
            precheck=precheck)
