"""Copy chosen pages ('1-3,5') of a PDF into a new PDF."""

from __future__ import annotations

from pathlib import Path

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.docops import parse_pages


class P(Params):
    input: str
    pages: str                        # "1-3,5"
    output: str


def _new_file(env: ToolEnv, raw: str, exts: tuple[str, ...]) -> Path:
    out = env.paths.resolve(raw)
    env.paths.check_write(out)
    if out.suffix.lower() not in exts:
        raise PolicyDenied(f"the file name must end with one of {', '.join(exts)}")
    if out.exists():
        raise PolicyDenied(f"{out} already exists — choose another name")
    return out


def _src(p: P, env: ToolEnv) -> Path:
    src = env.paths.resolve(p.input)
    env.paths.check_read(src)
    if not src.is_file() or src.suffix.lower() != ".pdf":
        raise PolicyDenied(f"{src} is not a PDF file")
    return src


def precheck(p: P, env: ToolEnv) -> None:
    _src(p, env)
    _new_file(env, p.output, (".pdf",))


async def run(p: P, env: ToolEnv) -> ToolResult:
    from pypdf import PdfReader, PdfWriter
    src = _src(p, env)
    out = _new_file(env, p.output, (".pdf",))
    reader = PdfReader(str(src))
    try:
        pages = parse_pages(p.pages, len(reader.pages))
    except ValueError as e:
        return ToolResult(False, str(e), {"input": str(src)}, {"exists": False})
    writer = PdfWriter()
    for i in pages:
        writer.add_page(reader.pages[i])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        writer.write(f)
    return ToolResult(True, f"pages {p.pages} → {out} ({len(pages)} pages)",
                      {"output": str(out), "pages": len(pages)},
                      {"output": str(out), "exists": True, "pages": len(pages)})


TOOL = Tool(name="pdf_split", params=P, run=run, action="file.create",
            target=lambda p: p.output, summary=lambda p: f"Pages {p.pages} of {p.input}",
            precheck=precheck)
