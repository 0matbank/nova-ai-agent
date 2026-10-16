"""documents skill (Phase 20, plan §34, §56): write a report / sheet and read it back;
PDFs merged and split; nothing is overwritten."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.skills.api import PolicyDenied
from tests.mocks.skills import SkillEnv, make_skill_env


@pytest.fixture
def s(tmp_path: Path) -> SkillEnv:
    return make_skill_env(tmp_path)


def make_pdf(path: Path, pages: list[str]) -> None:
    """A tiny valid PDF with one line of text per page (no PDF library needed)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    font = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        page, content = 3 + 2 * i, 4 + 2 * i
        kids.append(f"{page} 0 R")
        stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Contents {content} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def test_report_written_as_word_and_read_back(s: SkillEnv) -> None:
    out = s.workspace / "report.docx"
    content = ("# Status\nAll systems normal.\n- disk ok\n- ram ok\n1. first\n"
               "- [x] backup done\n- [ ] update pending")
    r = s.call("documents", "create", {"path": str(out), "title": "Weekly report",
                                       "content": content})
    assert r.ok and r.evidence["exists"] and s.tasks.buttons == []   # BLUE: no approval
    back = s.call("documents", "read", {"path": str(out)})
    text = back.data["text"]
    assert back.ok and back.untrusted
    assert "Weekly report" in text and "All systems normal." in text and "disk ok" in text
    assert "☑ backup done" in text and "☐ update pending" in text
    with pytest.raises(PolicyDenied, match="already exists"):
        s.call("documents", "create", {"path": str(out), "content": "x"})


def test_markdown_and_sheets(s: SkillEnv) -> None:
    md = s.workspace / "notes.md"
    assert s.call("documents", "create", {"path": str(md), "title": "Notes",
                                          "content": "- a"}).ok
    assert md.read_text(encoding="utf-8").startswith("# Notes")
    xl = s.workspace / "sales.xlsx"
    r = s.call("documents", "sheet_create", {"path": str(xl), "rows": [
        ["item", "qty", "price"], ["tea", 2, 30.5], ["rice", 1, 70]]})
    assert r.ok and r.evidence["rows"] == 3
    back = s.call("documents", "read", {"path": str(xl)}).data["text"]
    assert "item | qty | price" in back and "tea | 2 | 30.5" in back
    csv = s.workspace / "sales.csv"
    assert s.call("documents", "sheet_create", {"path": str(csv),
                                                "rows": [["a", "b"], [1, 2]]}).ok
    assert "1 | 2" in s.call("documents", "read", {"path": str(csv)}).data["text"]


def test_pdf_merge_split_and_read(s: SkillEnv) -> None:
    a, b = s.workspace / "a.pdf", s.workspace / "b.pdf"
    make_pdf(a, ["Alpha one", "Alpha two"])
    make_pdf(b, ["Beta one"])
    m = s.call("documents", "pdf_merge", {"inputs": [str(a), str(b)],
                                          "output": str(s.workspace / "all.pdf")})
    assert m.ok and m.evidence["pages"] == 3
    sp = s.call("documents", "pdf_split", {"input": m.data["output"], "pages": "2-3",
                                           "output": str(s.workspace / "part.pdf")})
    assert sp.ok and sp.evidence["pages"] == 2
    text = s.call("documents", "read", {"path": sp.data["output"]}).data["text"]
    assert "Alpha two" in text and "Beta one" in text and "Alpha one" not in text
    bad = s.call("documents", "pdf_split", {"input": str(a), "pages": "5",
                                            "output": str(s.workspace / "no.pdf")})
    assert not bad.ok and "outside" in bad.summary


def test_refusals(s: SkillEnv) -> None:
    with pytest.raises(PolicyDenied, match="end with"):
        s.call("documents", "create", {"path": str(s.workspace / "x.exe"), "content": "x"})
    with pytest.raises(PolicyDenied):
        s.call("documents", "read", {"path": str(s.root / "secrets" / ".env")})
    odd = s.workspace / "tool.exe"
    odd.write_bytes(b"MZ")
    with pytest.raises(PolicyDenied, match="can read"):
        s.call("documents", "read", {"path": str(odd)})
