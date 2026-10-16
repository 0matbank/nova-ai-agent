"""Phase 20 live drill — "General PC work expands": media + document + archive work
from plain requests, planned by the REAL local model, every result checked on disk
by the verifier (scratch folder only; nothing else on the PC is touched).

    uv run python scripts/media_docs_drill.py

PASS = every request reaches the right agent, completes with ✅ PASS, and the file
it made really exists (thumbnail jpg, Word report with the words, merged PDF with
all pages, zip with the photos); the info answer has the real duration/resolution.
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config, load_secrets  # noqa: E402
from core.orchestrator.executor import UserRequestExecutor  # noqa: E402
from core.router.intent import IntentRouter  # noqa: E402
from core.skills.mediaops import ffmpeg_exe  # noqa: E402
from models.router import ProviderRouter  # noqa: E402
from providers.registry import ProviderRegistry  # noqa: E402
from skills.documents.tests.test_skill_documents import make_pdf  # noqa: E402
from tests.mocks.skills import make_skill_env  # noqa: E402


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nova-media-drill-"))
    try:
        return await run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def run(tmp: Path) -> int:
    s = make_skill_env(tmp)
    box = s.workspace / "drill"
    (box / "photos").mkdir(parents=True)
    clip = box / "clip.mp4"
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",  # noqa: S603
                    "testsrc=duration=6:size=640x360:rate=25", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=6", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
    make_pdf(box / "a.pdf", ["Invoice page one", "Invoice page two"])
    make_pdf(box / "b.pdf", ["Receipt page"])
    for n in ("sea.jpg", "hill.jpg"):
        (box / "photos" / n).write_bytes(b"\xff\xd8 fake jpg")
    cfg = load_config(tmp / "config", tmp / "rt")
    adapters = ProviderRegistry.from_config(
        cfg, load_secrets(load_config().path("secrets_dir"))).adapters
    router = ProviderRouter(cfg.providers, adapters)
    s.tasks.engine.register("user_request",
                            UserRequestExecutor(IntentRouter(router), router, s.runner))

    async def ask(text: str) -> tuple[str, str, str, str]:
        t0 = time.time()
        tid = int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                       channel="telegram", chat_id="555").id)
        for _ in range(6):
            if not await s.tasks.engine.run_once():
                break
        t = s.tasks.store.get(tid)
        a = (next(st for st in t.steps if st.seq == 1).checkpoint or {}).get("assignment") or {}
        done = next((m for _, m in s.tasks.sent if f"TASK #{tid} COMPLETE" in m), "")
        verdict = done.split("Verifier:", 1)[-1].strip() if done else ""
        answer = t.result_summary or t.error_message or ""
        print(f"\n> {text}\n  → {a.get('lead')} · {t.state.value} in {time.time() - t0:.0f}s\n"
              f"  answer: {answer[:300]}\n  verdict: {verdict[:300]}")
        return str(a.get("lead")), t.state.value, answer, verdict

    checks: list[tuple[str, bool]] = []
    lead, st, _, v = await ask(f"{clip} video ta theke 2 second e ekta thumbnail banao")
    thumbs = list(box.glob("clip_thumb*.jpg"))
    checks.append(("thumbnail", lead == "media" and st == "COMPLETED" and "✅ PASS" in v
                   and bool(thumbs)))
    lead, st, a, v = await ask(f"{clip} video tar duration koto ar resolution ki?")
    checks.append(("video info", lead == "media" and st == "COMPLETED"
                   and "640" in a and "360" in a and ("6" in a)))
    report = box / "weekly-report.docx"
    lead, st, _, v = await ask(f"{report} e ekta weekly report likho: disk thik ache, backup "
                               "baki ache, update pending")
    text = ""
    if report.exists():
        import docx
        text = " ".join(p.text for p in docx.Document(str(report)).paragraphs).lower()
    checks.append(("word report", lead == "file_document" and st == "COMPLETED"
                   and "✅ PASS" in v and ("backup" in text or "ব্যাকআপ" in text)))
    merged = box / "all.pdf"
    lead, st, _, v = await ask(f"{box / 'a.pdf'} ar {box / 'b.pdf'} jora lagiye {merged} "
                               "pdf banao")
    pages = 0
    if merged.exists():
        from pypdf import PdfReader
        pages = len(PdfReader(str(merged)).pages)
    checks.append(("pdf merge", lead == "file_document" and st == "COMPLETED" and pages == 3))
    lead, st, _, v = await ask(f"{box / 'photos'} folder ta zip koro")
    zips = list(box.glob("*.zip"))
    names = zipfile.ZipFile(zips[0]).namelist() if zips else []
    checks.append(("zip", lead == "file_document" and st == "COMPLETED" and len(names) == 2))
    print("\n" + "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" for name, ok in checks))
    ok = all(ok for _, ok in checks)
    print(f"PHASE 20 MEDIA + DOCUMENTS DRILL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
