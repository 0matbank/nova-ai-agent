"""Write a report / checklist / summary as Word (.docx), Markdown or text."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from core.skills.api import Params, PolicyDenied, Tool, ToolEnv, ToolResult
from core.skills.docops import write_docx

EXTS = (".docx", ".md", ".txt")


class P(Params):
    path: str
    content: str = Field(max_length=200_000)   # '# heading', '- bullet', '- [ ] item'
    title: str | None = None


def _new_file(env: ToolEnv, raw: str, exts: tuple[str, ...]) -> Path:
    out = env.paths.resolve(raw)
    env.paths.check_write(out)
    if out.suffix.lower() not in exts:
        raise PolicyDenied(f"the file name must end with one of {', '.join(exts)}")
    if out.exists():
        raise PolicyDenied(f"{out} already exists — choose another name")
    return out


def precheck(p: P, env: ToolEnv) -> None:
    _new_file(env, p.path, EXTS)


async def run(p: P, env: ToolEnv) -> ToolResult:
    out = _new_file(env, p.path, EXTS)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".docx":
        blocks = write_docx(out, p.title, p.content)
    else:
        text = (f"# {p.title}\n\n" if p.title and out.suffix.lower() == ".md" else
                f"{p.title}\n\n" if p.title else "") + p.content
        out.write_text(text, encoding="utf-8")
        blocks = len([ln for ln in p.content.splitlines() if ln.strip()])
    return ToolResult(True, f"created {out} ({blocks} blocks)", {"output": str(out)},
                      {"output": str(out), "exists": out.is_file(), "size": out.stat().st_size})


TOOL = Tool(name="create", params=P, run=run, action="file.create", target=lambda p: p.path,
            summary=lambda p: f"Create the document {p.path}", precheck=precheck)
