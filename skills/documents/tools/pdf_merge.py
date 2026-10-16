"""Join PDFs into one new PDF."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult


class P(Params):
    inputs: list[str] = Field(min_length=2, max_length=100)
    output: str


def _new_file(env: ToolEnv, raw: str, exts: tuple[str, ...]) -> Path:
    out = env.paths.resolve(raw)
    env.paths.check_write(out)
    if out.suffix.lower() not in exts:
        raise PolicyDenied(f"the file name must end with one of {', '.join(exts)}")
    if out.exists():
        raise PolicyDenied(f"{out} already exists — choose another name")
    return out


def _inputs(p: P, env: ToolEnv) -> list[Path]:
    paths = []
    for raw in p.inputs:
        path = env.paths.resolve(raw)
        env.paths.check_read(path)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise PolicyDenied(f"{path} is not a PDF file")
        paths.append(path)
    return paths


def precheck(p: P, env: ToolEnv) -> None:
    _inputs(p, env)
    _new_file(env, p.output, (".pdf",))


async def run(p: P, env: ToolEnv) -> ToolResult:
    from pypdf import PdfReader, PdfWriter
    srcs = _inputs(p, env)
    out = _new_file(env, p.output, (".pdf",))
    writer = PdfWriter()
    for s in srcs:
        for page in PdfReader(str(s)).pages:
            writer.add_page(page)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        writer.write(f)
    pages = len(PdfReader(str(out)).pages)
    return ToolResult(True, f"merged {len(srcs)} PDFs → {out} ({pages} pages)",
                      {"output": str(out), "pages": pages},
                      {"output": str(out), "exists": True, "pages": pages})


TOOL = Tool(name="pdf_merge", params=P, run=run, action="file.create",
            target=lambda p: p.output, summary=lambda p: f"Merge {len(p.inputs)} PDFs",
            precheck=precheck)
