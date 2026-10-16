"""Creative foundation live drill — a REAL Creative Director (provider router:
Antigravity / Gemini / local) edits generated footage end to end (scratch folder only):

  footage: 4 s colour bars + tone · 2 s BLACK + silence · 4 s another pattern + tone
  request: "instagram reel banao, kalo ar chup thaka ongsho baad dao, caption dao,
            fade in-out"

    uv run python scripts/creative_drill.py

PASS = COMPLETED with ✅ PASS; the reel is 9:16; QC passed; the black/silent middle
is gone (no black stretch in the result); plan.json + .otio exist; the source is
unchanged.
"""

import asyncio
import hashlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config, load_secrets  # noqa: E402
from core.creative.analyze import analyze  # noqa: E402
from core.memory import ProjectMemory  # noqa: E402
from core.orchestrator.executor import UserRequestExecutor  # noqa: E402
from core.router.intent import IntentRouter  # noqa: E402
from core.skills.mediaops import ffmpeg_exe  # noqa: E402
from models.router import ProviderRouter  # noqa: E402
from providers.registry import ProviderRegistry  # noqa: E402
from tests.mocks.skills import make_skill_env  # noqa: E402


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nova-creative-drill-"))
    try:
        return await run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def run(tmp: Path) -> int:
    s = make_skill_env(tmp)
    clip = s.workspace / "footage.mp4"
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error",  # noqa: S603
                    "-f", "lavfi", "-i", "testsrc=duration=4:size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "color=black:s=1280x720:d=2:r=30",
                    "-f", "lavfi", "-i", "testsrc2=duration=4:size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=f=440:d=4",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=2",
                    "-f", "lavfi", "-i", "sine=f=660:d=4", "-filter_complex",
                    "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v];[3:a]aresample=48000,"
                    "aformat=channel_layouts=stereo[a0];[4:a]aformat=channel_layouts=stereo[a1];"
                    "[5:a]aresample=48000,aformat=channel_layouts=stereo[a2];"
                    "[a0][a1][a2]concat=n=3:v=0:a=1[a]", "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)],
                   check=True)
    before = hashlib.sha256(clip.read_bytes()).hexdigest()
    cfg = load_config(tmp / "config", tmp / "rt")
    adapters = ProviderRegistry.from_config(
        cfg, load_secrets(load_config().path("secrets_dir"))).adapters
    router = ProviderRouter(cfg.providers, adapters)
    s.tasks.engine.register("user_request", UserRequestExecutor(
        IntentRouter(router), router, s.runner, memory=ProjectMemory(s.tasks.store._sessions)))
    text = (f"{clip} theke instagram er jonno ekta reel banao: majhe kalo ar chup thaka "
            "ongsho ta baad dao, shuru te caption dao 'Nova Test', fade in ar fade out dao")
    t0 = time.time()
    tid = int(s.tasks.store.create(title=text, request_text=text, task_type="user_request",
                                   channel="telegram", chat_id="555").id)
    for _ in range(6):
        if not await s.tasks.engine.run_once():
            break
    t = s.tasks.store.get(tid)
    done = next((m for _, m in s.tasks.sent if f"TASK #{tid} COMPLETE" in m), "")
    print(f"{t.state.value} in {time.time() - t0:.0f}s\n{(t.result_summary or t.error_message
                                                           or '')[:1500]}")
    print("\nVerifier:", done.split("Verifier:", 1)[-1].strip()[:600] if done else "-")
    out = clip.with_name("footage_nova_edit.mp4")
    rep = await analyze(out) if out.is_file() else None
    ok = (t.state.value == "COMPLETED" and "✅ PASS" in done and rep is not None
          and (rep.width, rep.height) == (1080, 1920) and not [b for b in rep.black
                                                             if b[1] - b[0] > 1.0]
          and out.with_suffix(".otio").is_file() and out.with_suffix(".plan.json").is_file()
          and hashlib.sha256(clip.read_bytes()).hexdigest() == before)
    if rep is not None:
        print(f"\nresult: {rep.summary()}")
    print(f"CREATIVE DRILL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
