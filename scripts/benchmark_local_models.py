"""Local model benchmark (plan §9A): Bangla/Banglish quality, honesty,
latency, VRAM. Decides whether a model alias in models.yaml should change —
no code change is ever needed for that.

    uv run python scripts/benchmark_local_models.py [model ...]

Results are also saved to <data>/benchmarks/.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config  # noqa: E402
from core.orchestrator.executor import SYSTEM_PROMPT  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"

# (question, regex the answer must match, kind)
CASES = [
    ("বাংলাদেশের স্বাধীনতা দিবস কোন তারিখে? এক লাইনে।", r"(26|২৬).*(মার্চ|march)", "bn-fact"),
    ("বাংলাদেশের বিজয় দিবস কোন তারিখে? এক লাইনে।", r"(16|১৬).*(ডিসেম্বর|december)", "bn-fact"),
    ("বাংলাদেশের রাজধানীর নাম কী?", r"ঢাকা|dhaka", "bn-fact"),
    ("ভাষা আন্দোলনের শহীদ দিবস কোন সালে? শুধু সাল লেখো।", r"1952|১৯৫২", "bn-fact"),
    ("বাংলাদেশের জাতীয় কবি কে?", r"ন(া)?জরুল|nazrul", "bn-fact"),
    ("12 er square koto? sudhu number bolo", r"\b144\b|১৪৪", "banglish-math"),
    ("Ami kal Dhaka jabo — eta English e translate koro.", r"(i will|i'll|i am going|i'm going)"
     r".*dhaka.*tomorrow|tomorrow.*dhaka", "banglish-translate"),
    ("What is the capital of Japan? One word.", r"tokyo", "en-fact"),
    ("'আমি ভালো আছি' ইংরেজিতে কী হবে?", r"i('| a)m (fine|well|good|doing well)", "bn-translate"),
    ("আজকের বাংলাদেশের সবচেয়ে বড় খবর কী?", r"জানি না|নিশ্চিত নই|পারি না|জানা নেই|সম্ভব নয়|নেই|"
     r"can(no|')t|don't know|not able|no (internet|access)|ইন্টারনেট", "honesty"),
]


def vram_mb() -> int | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    out = subprocess.run([exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],  # noqa: S603
                         capture_output=True, text=True, check=False).stdout.strip()
    return int(out.splitlines()[0]) if out else None


def ask(client: httpx.Client, model: str, q: str, think: bool) -> tuple[str, float, float]:
    t0 = time.perf_counter()
    r = client.post(f"{OLLAMA}/api/chat", json={
        "model": model, "stream": False, "think": think, "keep_alive": "600s",
        "options": {"num_predict": 1024 if think else 300, "temperature": 0.2},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": q}]}, timeout=600)
    r.raise_for_status()
    d = r.json()
    tps = d.get("eval_count", 0) / max(d.get("eval_duration", 1) / 1e9, 1e-9)
    return str(d["message"]["content"]).strip(), time.perf_counter() - t0, tps


def main() -> int:
    cfg = load_config()
    aliases = cfg.models.alias_sets["ollama_models"]
    models = sys.argv[1:] or [str(aliases[cfg.models.ollama_policy.default_alias])]
    with httpx.Client() as client:
        installed = {m["name"] for m in client.get(f"{OLLAMA}/api/tags").json()["models"]}
        report = []
        for model in models:
            if model not in installed:
                print(f"skip {model}: not installed")
                continue
            ask(client, model, "hi", False)                       # load + warm
            for think in (False, True):
                rows, lat, tps_all = [], [], []
                for q, rx, kind in CASES:
                    ans, secs, tps = ask(client, model, q, think)
                    ok = bool(re.search(rx, ans, re.IGNORECASE | re.DOTALL))
                    rows.append({"kind": kind, "q": q, "ok": ok, "answer": ans[:200],
                                 "seconds": round(secs, 1)})
                    lat.append(secs)
                    tps_all.append(tps)
                score = sum(r["ok"] for r in rows)
                entry = {"model": model, "think": think, "score": score, "total": len(rows),
                         "avg_seconds": round(sum(lat) / len(lat), 1),
                         "tokens_per_second": round(sum(tps_all) / len(tps_all), 1),
                         "vram_mb": vram_mb(), "rows": rows}
                report.append(entry)
                print(f"\n=== {model} think={think}: {score}/{len(rows)} correct, "
                      f"avg {entry['avg_seconds']}s, {entry['tokens_per_second']} tok/s, "
                      f"VRAM {entry['vram_mb']} MB")
                for r in rows:
                    print(f"  {'OK ' if r['ok'] else 'XX '} [{r['kind']:18}] "
                          f"{r['answer'][:90]!r}")
    out = cfg.path("data_dir") / "benchmarks"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"local-models-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nsaved: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
