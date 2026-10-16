"""Quality control of a render (Creative pipeline step 7, plan §54): the result is
measured again with ffmpeg — never assumed. A failed check means the edit is NOT
done; Nova says what is wrong instead of claiming success."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.creative.analyze import FootageReport, analyze
from core.creative.plan import EditPlan


@dataclass
class QCResult:
    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    report: FootageReport | None = None

    def lines(self) -> list[str]:
        return [f"{'✓' if ok else '✗'} {name}: {detail}" for name, ok, detail in self.checks]


def _allowed_dark(plan: EditPlan) -> list[tuple[float, float]]:
    """Where black is intended: fades and dips (with a little slack)."""
    win: list[tuple[float, float]] = []
    total = plan.total_seconds()
    if plan.look.fade_in:
        win.append((0, plan.look.fade_in + 0.3))
    if plan.look.fade_out:
        win.append((total - plan.look.fade_out - 0.3, total + 1))
    if plan.look.transitions == "dip":
        t = 0.0
        for c in plan.clips[:-1]:
            t += c.length
            win.append((t - plan.look.transition_seconds - 0.3,
                        t + plan.look.transition_seconds + 0.3))
    return win


async def check_render(path: Path, plan: EditPlan, size: tuple[int, int],
                       sources: list[FootageReport]) -> QCResult:
    q = QCResult(ok=True)

    def add(name: str, ok: bool, detail: str) -> None:
        q.checks.append((name, ok, detail))
        q.ok = q.ok and ok

    if not path.is_file() or path.stat().st_size == 0:
        add("file", False, "no output file")
        return q
    rep = await analyze(path)
    q.report = rep
    want = plan.total_seconds()
    tol = max(0.5, want * 0.02)
    add("length", abs(rep.duration - want) <= tol, f"{rep.duration:.2f}s (plan {want:.2f}s)")
    add("size", (rep.width, rep.height) == size, f"{rep.width}x{rep.height} (want "
                                                 f"{size[0]}x{size[1]})")
    # black stretches the plan did not ask for (sources' own black is the director's call)
    allowed = _allowed_dark(plan)
    src_black = any(r.black for r in sources)
    stray = [(a, b) for a, b in rep.black
             if b - a > 1.0 and not any(a >= lo and b <= hi for lo, hi in allowed)]
    add("no unplanned black", not stray or src_black,
        "none" if not stray else ", ".join(f"{a:.1f}–{b:.1f}s" for a, b in stray)
        + (" (also in the source)" if src_black else ""))
    # motion (owner 2026-10-16: "video cholaii nah, stop picture"): the result may be
    # still only where the source already was, or inside planned fades / dips
    expected = sum(max(0.0, min(b, c.end) - max(a, c.start))
                   for c in plan.clips for a, b in sources[c.source].frozen)
    expected += sum(hi - lo for lo, hi in _allowed_dark(plan))
    frozen = sum(b - a for a, b in rep.frozen)
    add("video moves", frozen <= expected + 1.0,
        f"still for {frozen:.1f}s of {rep.duration:.1f}s"
        + (f" (source/fades allow {expected:.1f}s)" if frozen > expected + 1.0 else ""))
    expect_audio = plan.audio.music is not None or (
        not plan.audio.mute_original and any(r.has_audio for r in sources))
    if expect_audio:
        add("audio present", rep.has_audio and (rep.mean_db or -120) > -50,
            f"mean {rep.mean_db} dB")
        add("no clipping", (rep.max_db if rep.max_db is not None else 0) < -0.05,
            f"max {rep.max_db} dB")
    return q
