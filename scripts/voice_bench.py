"""V1.1 voice benchmark: transcription accuracy + routing + latency on a fixed corpus.

    uv run python scripts/voice_bench.py --make            # speak the corpus (edge-tts)
    uv run python scripts/voice_bench.py --run --label before
    uv run python scripts/voice_bench.py --compare before after

The corpus (scripts/voice_corpus.json) is spoken by Microsoft neural voices, so it is
a repeatable stand-in for the owner's voice — the real check is the owner's phone test.
Audio and results live outside the repo (workspace/voice-corpus).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import load_config  # noqa: E402

CORPUS = ROOT / "scripts" / "voice_corpus.json"


def out_dir() -> Path:
    d = load_config().path("workspace_dir") / "voice-corpus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def items() -> list[dict[str, Any]]:
    data: dict[str, Any] = json.loads(CORPUS.read_text(encoding="utf-8"))
    return list(data["items"])


async def make() -> None:
    import av
    import edge_tts

    d = out_dir()
    for it in items():
        mp3 = d / f"{it['id']}.mp3"
        ogg = d / f"{it['id']}.ogg"
        if ogg.exists():
            continue
        await edge_tts.Communicate(it["say"], it["voice"]).save(str(mp3))
        # Telegram voice notes are Opus in Ogg, mono 48 kHz
        with av.open(str(mp3)) as src, av.open(str(ogg), "w", format="ogg") as dst:
            stream = dst.add_stream("libopus", rate=48000, layout="mono")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
            for frame in src.decode(audio=0):
                for f in resampler.resample(frame):
                    for p in stream.encode(f):
                        dst.mux(p)
            for p in stream.encode(None):
                dst.mux(p)
        mp3.unlink()
        print("made", ogg.name)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def judge(expect: dict[str, Any], routed: dict[str, Any]) -> bool:
    if "app" in expect:
        want = str(expect["app"])
        if want.startswith("ask:"):
            return routed.get("ask") is not None and any(
                _norm(want[4:]) in _norm(c) for c in routed["ask"])
        skill = routed.get("skill")
        return bool(skill and skill[0] == "app-control" and skill[1] == expect["action"]
                    and _norm(want) in _norm(str(skill[2].get("app", ""))))
    if routed.get("category") != expect["intent"] and not (
            expect["intent"] == "question" and routed.get("category") in (None, "question")):
        return False
    if "skill" in expect:
        skill = routed.get("skill")
        return bool(skill and f"{skill[0]}.{skill[1]}" == expect["skill"])
    return True


def local_ai_router() -> Any:
    """Only the local model (no cloud quota spent by a benchmark)."""
    from core.config.secrets import load_secrets
    from models.router import ProviderRouter
    from providers.registry import ProviderRegistry
    cfg = load_config()
    adapters = ProviderRegistry.from_config(cfg, load_secrets(cfg.path("secrets_dir"))).adapters
    return ProviderRouter(cfg.providers, {"ollama_local": adapters["ollama_local"]})


def router_for_bench(ai: bool = False) -> Any:
    """The routing the running code has: the V1.1 app resolver when present."""
    try:
        from core.apps.service import build_app_resolver
    except ImportError:
        build_app_resolver = None
    from core.router.intent import IntentRouter, classify_by_rules
    if build_app_resolver is None:
        def route_old(text: str) -> dict[str, Any]:
            hit = classify_by_rules(text)
            return {"category": hit.category if hit else None,
                    "skill": list(hit.skill) if hit and hit.skill else None}
        return route_old
    resolver = build_app_resolver(load_config(), scan_if_missing=True)
    router = IntentRouter(local_ai_router() if ai else None, apps=resolver)

    loop = asyncio.new_event_loop()          # one loop: the AI client keeps its connection

    def route_new(text: str, chat: str = "bench") -> dict[str, Any]:
        hit = loop.run_until_complete(router.classify(text, "bench", chat))
        return {"category": hit.category,
                "skill": list(hit.skill) if hit.skill else None,
                "ask": hit.meta.get("app_choices")}
    return route_new


def make_transcriber(cfg: Any) -> Any:
    """V1.1 code has core.service.build_transcriber; the V1 baseline built it inline."""
    try:
        from core.service import build_transcriber
    except ImportError:
        build_transcriber = None
    if build_transcriber is not None:
        return build_transcriber(cfg)
    from core.voice.transcriber import Transcriber
    w = cfg.models.whisper
    path = w.path if w.path.is_absolute() else cfg.root / w.path
    return Transcriber(path, w.device, w.compute_type, cfg.resources.idle_unload_seconds.whisper,
                       cfg.default.voice.beam_size, hint_words=cfg.default.voice.hint_words,
                       vad_speech_pad_ms=cfg.default.voice.vad_speech_pad_ms)


def vram_used_mb() -> int | None:
    import subprocess
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",  # noqa: S607
                              "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=10).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def run(label: str, limit: int | None, ai: bool = False, hotwords: int = 0,
        model: str | None = None) -> None:
    import psutil
    cfg = load_config()
    if model:
        cfg = cfg.model_copy(update={"models": cfg.models.model_copy(update={
            "whisper": cfg.models.whisper.model_copy(update={"path": Path(model)})})})
    tr = make_transcriber(cfg)
    d = out_dir()
    route = router_for_bench(ai)
    if hotwords:
        from core.apps.service import build_app_resolver
        tr.hotwords = ", ".join(build_app_resolver(cfg).names(hotwords))
        print("hotwords:", tr.hotwords)
    proc = psutil.Process()
    rss0, vram0 = proc.memory_info().rss, vram_used_mb()
    t0 = time.perf_counter()
    tr.warm_sync() if hasattr(tr, "warm_sync") else tr._ensure()
    load = time.perf_counter() - t0
    mem = {"model": str(cfg.models.whisper.path),
           "rss_gb_added": round((proc.memory_info().rss - rss0) / 2**30, 2),
           "vram_mb_added": (vram_used_mb() - vram0) if vram0 is not None else None,
           "ram_percent_after_load": psutil.virtual_memory().percent}
    rows = []
    for it in items()[:limit]:
        audio = d / f"{it['id']}.ogg"
        t = tr.transcribe_sync(audio)
        r0 = time.perf_counter()
        routed = route(t.text, it["id"]) if route.__name__ == "route_new" else route(t.text)
        route_s = time.perf_counter() - r0
        ok = judge(it["expect"], routed)
        rows.append({"id": it["id"], "said": it["say"], "heard": t.text, "lang": t.language,
                     "conf": round(t.confidence, 2), "stt_s": round(t.seconds, 2),
                     "route_s": round(route_s, 3), "routed": routed, "ok": ok})
        to = routed.get("skill") or routed.get("ask") or routed.get("category")
        print(f"{'PASS' if ok else 'FAIL'} {it['id']:<20} {t.seconds:5.2f}s {t.language} "
              f"{t.confidence:.2f}  «{t.text}» → {to}")
    passed = sum(r["ok"] for r in rows)
    stt = sorted(r["stt_s"] for r in rows)
    apps = [r for r, it in zip(rows, items()[:limit], strict=True) if "app" in it["expect"]]
    summary = {"label": label, "load_s": round(load, 1), "passed": passed, "total": len(rows),
               "app_passed": sum(r["ok"] for r in apps), "app_total": len(apps),
               "stt_median_s": stt[len(stt) // 2], "stt_max_s": stt[-1],
               "peak_ram_percent": psutil.virtual_memory().percent,
               "device": getattr(tr, "_loaded_on", ""), **mem}
    print(json.dumps(summary, ensure_ascii=False))
    (d / f"results-{label}.json").write_text(json.dumps({"summary": summary, "rows": rows},
                                                        ensure_ascii=False, indent=1),
                                             encoding="utf-8")


def compare(labels: list[str]) -> None:
    """Same corpus, several runs side by side. 'heard' = how close the transcript sounds
    to what was said (script-independent sound key, 0–100), so a Bangla-script
    'প্রিমিয়ার প্রো' and 'Premiere Pro' both count as heard right."""
    from core.apps.phonetic import key, similarity
    d = out_dir()
    runs = {x: json.loads((d / f"results-{x}.json").read_text(encoding="utf-8"))
            for x in labels}
    ids = [it["id"] for it in items()]
    print(f"{'run':<22}{'routed ok':>11}{'app names':>11}{'heard':>7}{'stt med':>9}"
          f"{'load':>7}{'RAM+GB':>8}{'VRAM+MB':>9}{'RAM%':>6}")
    for x, r in runs.items():
        s, rows = r["summary"], {row["id"]: row for row in r["rows"]}
        same = [rows[i] for i in ids if i in rows]
        heard = sum(similarity(key(z["said"]), key(z["heard"])) for z in same) / len(same)
        ok = sum(z["ok"] for z in same)
        print(f"{x:<22}{ok:>6}/{len(same):<4}{s.get('app_passed', '?')!s:>6}/"
              f"{s.get('app_total', '?')!s:<4}{heard * 100:>6.0f}{s['stt_median_s']:>9.2f}"
              f"{s['load_s']:>7.1f}{s.get('rss_gb_added', '?')!s:>8}"
              f"{s.get('vram_mb_added', '?')!s:>9}{s.get('peak_ram_percent', '?')!s:>6}")
    if len(runs) >= 2:
        first, last = labels[0], labels[-1]
        a_rows = {z["id"]: z for z in runs[first]["rows"]}
        for z in runs[last]["rows"]:
            y = a_rows.get(z["id"])
            if y is not None and y["ok"] != z["ok"]:
                print(f"  {z['id']:<20} {first}:{'PASS' if y['ok'] else 'FAIL'} «{y['heard']}»"
                      f" → {last}:{'PASS' if z['ok'] else 'FAIL'} «{z['heard']}»")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--make", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--label", default="run")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ai", action="store_true", help="local AI fallback for unclear names")
    ap.add_argument("--hotwords", type=int, default=0, help="installed app names for Whisper")
    ap.add_argument("--model", help="another whisper model folder")
    ap.add_argument("--compare", nargs="+")
    a = ap.parse_args()
    if a.make:
        asyncio.run(make())
    if a.run:
        run(a.label, a.limit, a.ai, a.hotwords, a.model)
    if a.compare:
        compare(a.compare)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
