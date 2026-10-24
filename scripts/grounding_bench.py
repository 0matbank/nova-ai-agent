"""Grounding benchmark (plan §72 CU-8, Report E protocol): the same screenshots and the
same targets for every vision model — accuracy (the point lands inside the real control),
speed and VRAM. Ground truth comes from the accessibility tree (or, for the custom-drawn
canvas, from the app's own drawing), never from a model.

    uv run python scripts/grounding_bench.py collect          # lab screenshots + targets
    uv run python scripts/grounding_bench.py run qwen3-vl     # one model on the set
    uv run python scripts/grounding_bench.py report           # all results, one table

Only private lab apps are opened (tests/mocks/cu_lab.py). A model that cannot run is
reported NOT_TESTED — never guessed. Results: <data>/benchmarks/grounding/.
"""

from __future__ import annotations

import base64
import json
import math
import re
import statistics
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "workers" / "desktop-worker"))

from core.config import load_config  # noqa: E402
from core.orchestrator.vision import PROMPT, TARGET_ONLY, parse_box  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
SET_KINDS = ("winforms", "wpf", "win32_dialog", "web", "canvas", "dense")
NOUN = {"Button": "button", "Edit": "text field", "CheckBox": "checkbox",
        "RadioButton": "radio button", "ComboBox": "dropdown", "Hyperlink": "link",
        "TabItem": "tab", "ListItem": "list item", "MenuItem": "menu item",
        "Document": "text area"}
CANVAS_BG = (0x20, 0x23, 0x2A)
CANVAS_BUTTONS = [("Render", 60), ("Pause", 210), ("Delete job", 360)]   # tk_canvas.py
# UI-TARS-1.5 grounding prompt (its own format: one point, absolute pixels of the
# model-resized image)
UITARS_PROMPT = ("Output only the coordinate of one point in your response. "
                 "What element matches the following task: click {target}")
MODELS: dict[str, dict[str, Any]] = {
    # Nova's current setting: no thinking for vision (models.yaml think_task_types)
    "qwen3-vl": {"backend": "ollama", "tag": "qwen3-vl:8b", "style": "qwen", "think": False},
    "qwen3-vl-think": {"backend": "ollama", "tag": "qwen3-vl:8b", "style": "qwen",
                       "think": True},
    # Ollama does not load this model's vision part: llama.cpp's own server instead
    "ui-tars": {"backend": "llama", "tag": "UI-TARS-1.5-7B.Q4_K_M.gguf", "style": "uitars",
                "mmproj": "UI-TARS-1.5-7B.mmproj-f16.gguf"},
}
MODELS_DIR = ROOT.parent / "models"
LLAMA_SERVER = MODELS_DIR / "llama.cpp" / "llama-server.exe"
LLAMA_URL = "http://127.0.0.1:8091"
# safety (owner 2026-10-24): stop before the PC runs short of memory
MIN_FREE_RAM_MIB = 1500
MAX_GPU_USED_MIB = 15600


def bench_dir() -> Path:
    d = load_config().path("data_dir") / "benchmarks" / "grounding"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ the set

def _canvas_targets(img: Any) -> list[dict[str, Any]]:
    """The canvas draws its buttons itself: find the canvas by its colour, then use the
    app's own coordinates (scaled if Windows stretched the window)."""
    px = img.load()
    xs, ys = [], []
    for y in range(0, img.height, 2):
        for x in range(0, img.width, 2):
            if px[x, y][:3] == CANVAS_BG:
                xs.append(x)
                ys.append(y)
    left, top = min(xs), min(ys)
    scale = (max(xs) - left + 2) / 520
    return [{"name": n, "type": "Button", "query": f"the '{n}' button",
             "box": [round(left + x * scale), round(top + 120 * scale),
                     round(left + (x + 120) * scale), round(top + 170 * scale)]}
            for n, x in CANVAS_BUTTONS]


def _inside(a: list[int], b: list[int]) -> bool:
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def _uia_targets(obs: dict[str, Any]) -> list[dict[str, Any]]:
    ox, oy = obs["origin"]
    els = [e for e in obs["elements"] if not e.get("chrome") and e["type"] in NOUN
           and (e.get("name") or "").strip()
           and e["name"].strip() != obs["window"]["title"]]   # the page itself
    out = []
    for e in els:
        name = e["name"].strip()
        same = [o for o in els if o is not e and o["name"].strip().lower() == name.lower()]
        # a list item and the link inside it are one thing: keep the inner one
        if any(_inside(o["rect"], e["rect"]) for o in same):
            continue
        if any(not _inside(e["rect"], o["rect"]) for o in same):
            continue                                  # ambiguous: no single right answer
        left, top, right, bottom = e["rect"]
        out.append({"name": name, "type": e["type"],
                    "query": f"the '{name}' {NOUN[e['type']]}",
                    "box": [left - ox, top - oy, right - ox, bottom - oy]})
    return out


def collect() -> None:
    import desktop_observe
    from PIL import Image

    from tests.mocks.cu_lab import lab_app
    d = bench_dir()
    cases = []
    for kind in SET_KINDS:
        with lab_app(kind) as title:
            obs = desktop_observe.observe(d / "shots", window=title, focus=True)
        img = Image.open(obs["image"]).convert("RGB")
        shot = d / f"{kind}.png"
        img.save(shot)
        targets = _canvas_targets(img) if kind == "canvas" else _uia_targets(obs)
        cases += [{"kind": kind, "image": shot.name, "size": list(img.size), **t}
                  for t in targets]
        print(f"{kind}: {img.size[0]}x{img.size[1]}, {len(targets)} targets")
    (d / "set.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    print(f"{len(cases)} targets → {d / 'set.json'}")


# ------------------------------------------------------------------ models

def smart_resize(w: int, h: int, factor: int = 28, min_px: int = 100 * 28 * 28,
                 max_px: int = 16384 * 28 * 28) -> tuple[int, int]:
    """Qwen2.5-VL / UI-TARS image resize (the model's own coordinate space)."""
    rw, rh = max(factor, round(w / factor) * factor), max(factor, round(h / factor) * factor)
    if rw * rh > max_px:
        beta = math.sqrt(w * h / max_px)
        rw, rh = math.floor(w / beta / factor) * factor, math.floor(h / beta / factor) * factor
    elif rw * rh < min_px:
        beta = math.sqrt(min_px / (w * h))
        rw, rh = math.ceil(w * beta / factor) * factor, math.ceil(h * beta / factor) * factor
    return rw, rh


def to_point(style: str, answer: str, size: tuple[int, int]) -> tuple[float, float] | None:
    w, h = size
    if style == "qwen":
        box = parse_box(answer)
        if box is None:
            return None
        return (box[0] + box[2]) / 2 / 1000 * w, (box[1] + box[3]) / 2 / 1000 * h
    nums = re.findall(r"-?\d+(?:\.\d+)?", answer)
    if len(nums) < 2:
        return None
    x, y = float(nums[0]), float(nums[1])
    if len(nums) >= 4:                                # a box: its centre
        x, y = (x + float(nums[2])) / 2, (y + float(nums[3])) / 2
    # llama.cpp gives the model the image at its own size (only shrunk when huge); a
    # calibration on synthetic buttons (2026-10-24) showed answers in those pixels — the
    # transformers smart_resize upscale of small images does not happen there
    rw, rh = smart_resize(w, h, min_px=0) if w * h > 16384 * 28 * 28 else (w, h)
    return x / rw * w, y / rh * h


def ask(spec: dict[str, Any], png: bytes, query: str) -> tuple[str, float, int]:
    """→ (answer, seconds, output tokens)."""
    prompt = (PROMPT.format(what=TARGET_ONLY.format(target=query)) if spec["style"] == "qwen"
              else UITARS_PROMPT.format(target=query))
    if spec["backend"] == "llama":
        uri = "data:image/png;base64," + base64.b64encode(png).decode()
        t = time.perf_counter()
        r = httpx.post(f"{LLAMA_URL}/v1/chat/completions", timeout=300, json={
            "temperature": 0, "max_tokens": 64,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": uri}},
                {"type": "text", "text": prompt}]}]})
        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"]["content"], time.perf_counter() - t,
                int(data.get("usage", {}).get("completion_tokens", 0)))
    body: dict[str, Any] = {
        "model": spec["tag"], "stream": False, "keep_alive": "10m",
        "messages": [{"role": "user", "content": prompt,
                      "images": [base64.b64encode(png).decode()]}],
        "options": {"num_ctx": 16384}}
    if "think" in spec:
        body["think"] = spec["think"]
    if spec["style"] == "qwen":
        body["format"] = "json"
    if spec["style"] == "uitars":
        body["options"]["temperature"] = 0
    t = time.perf_counter()
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=300)
    r.raise_for_status()
    data = r.json()
    return data["message"]["content"], time.perf_counter() - t, int(data.get("eval_count", 0))


def _start_llama(spec: dict[str, Any]) -> subprocess.Popen[bytes]:
    d = MODELS_DIR / "ui-tars"
    proc = subprocess.Popen(  # noqa: S603
        [str(LLAMA_SERVER), "-m", str(d / spec["tag"]), "--mmproj", str(d / spec["mmproj"]),
         "-ngl", "99", "-c", "8192", "--host", "127.0.0.1", "--port", LLAMA_URL.rsplit(":", 1)[1],
         "--no-webui", "--load-mode", "none"],   # to the GPU, not mapped into RAM
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(180):
        try:
            if httpx.get(f"{LLAMA_URL}/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            break
        time.sleep(1)
    proc.kill()
    raise RuntimeError("llama-server did not start")


def _missing(spec: dict[str, Any]) -> str | None:
    if spec["backend"] == "llama":
        need = [LLAMA_SERVER, MODELS_DIR / "ui-tars" / spec["tag"],
                MODELS_DIR / "ui-tars" / spec["mmproj"]]
        gone = [p.name for p in need if not p.is_file()]
        return f"missing: {', '.join(gone)}" if gone else None
    tags = {m["name"] for m in httpx.get(f"{OLLAMA}/api/tags", timeout=10).json()["models"]}
    if spec["tag"] not in tags and f"{spec['tag']}:latest" not in tags:
        return f"{spec['tag']} is not installed"
    return None


def _loaded() -> list[dict[str, Any]]:
    return httpx.get(f"{OLLAMA}/api/ps", timeout=10).json().get("models", [])


def _unload_all() -> None:
    for m in _loaded():
        httpx.post(f"{OLLAMA}/api/generate", json={"model": m["name"], "keep_alive": 0},
                   timeout=60)
    for _ in range(30):
        if not _loaded():
            return
        time.sleep(1)


def _gpu_used_mib() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",  # noqa: S607
                          "--format=csv,noheader,nounits"], capture_output=True, text=True,
                         check=False)
    return int(out.stdout.strip().splitlines()[0]) if out.returncode == 0 else -1


def run(name: str, tag: str | None) -> None:
    spec = dict(MODELS[name])
    if tag:
        spec["tag"] = tag
    d = bench_dir()
    cases = json.loads((d / "set.json").read_text(encoding="utf-8"))
    result: dict[str, Any] = {"model": name, "tag": spec["tag"], "backend": spec["backend"],
                              "at": datetime.now(UTC).isoformat(timespec="seconds")}
    missing = _missing(spec)
    if missing:
        result["status"] = "NOT_TESTED"
        result["reason"] = missing
        print(json.dumps(result, indent=1))
        (d / f"result-{name}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        return
    if spec["backend"] == "ollama":
        info = httpx.post(f"{OLLAMA}/api/show", json={"model": spec["tag"]}, timeout=30).json()
        result["digest"] = next((m.get("digest", "") for m in
                                 httpx.get(f"{OLLAMA}/api/tags", timeout=10).json()["models"]
                                 if m["name"] in (spec["tag"], f"{spec['tag']}:latest")), "")
        result["quantization"] = info.get("details", {}).get("quantization_level")
    else:
        result["quantization"] = spec["tag"].rsplit(".", 2)[-2]
        result["projector"] = spec["mmproj"]
    import psutil
    _unload_all()                       # one model alone on the GPU (owner 2026-10-24)
    base = _gpu_used_mib()
    peak = {"gpu": base, "cpu": 0.0, "ram_free_min": psutil.virtual_memory().available >> 20}
    stop, abort = threading.Event(), threading.Event()
    psutil.cpu_percent()

    def sample() -> None:
        low = 0
        while not stop.is_set():
            gpu, free = _gpu_used_mib(), psutil.virtual_memory().available >> 20
            peak["gpu"] = max(peak["gpu"], gpu)
            peak["ram_free_min"] = min(peak["ram_free_min"], free)
            peak["cpu"] = max(peak["cpu"], psutil.cpu_percent())
            # sustained shortage (2 s), not a one-sample dip while a model loads
            low = low + 1 if free < MIN_FREE_RAM_MIB or gpu > MAX_GPU_USED_MIB else 0
            if low >= 4:
                abort.set()
            time.sleep(0.5)
    threading.Thread(target=sample, daemon=True).start()
    t_load = time.perf_counter()
    server = _start_llama(spec) if spec["backend"] == "llama" else None
    result["server_start_secs"] = round(time.perf_counter() - t_load, 1) if server else None
    rows = []
    try:
        for i, c in enumerate(cases):
            if abort.is_set():
                result["stopped"] = (f"memory guard: free RAM {peak['ram_free_min']} MiB, "
                                     f"GPU {peak['gpu']} MiB")
                break
            png = (d / c["image"]).read_bytes()
            try:
                answer, secs, tokens = ask(spec, png, c["query"])
            except (httpx.HTTPError, KeyError) as e:
                answer, secs, tokens = f"ERROR {e}", 0.0, 0
            pt = to_point(spec["style"], answer, tuple(c["size"]))
            x1, y1, x2, y2 = c["box"]
            hit = pt is not None and x1 <= pt[0] <= x2 and y1 <= pt[1] <= y2
            dist = (None if pt is None else round(math.dist(pt, ((x1 + x2) / 2, (y1 + y2) / 2))))
            rows.append({"kind": c["kind"], "name": c["name"], "hit": hit, "secs": round(secs, 2),
                         "point": None if pt is None else [round(pt[0]), round(pt[1])],
                         "dist": dist, "answer": answer[:200], "first": i == 0,
                         "tokens": tokens})
            print(f"{'✓' if hit else '✗'} {c['kind']:12} {c['name'][:24]:24} "
                  f"{secs:5.1f}s {answer[:60]!r}")
        vram = next((m.get("size_vram", 0) for m in _loaded()
                     if m["name"] in (spec["tag"], f"{spec['tag']}:latest")), 0)
    finally:
        stop.set()
        if server is not None:
            server.kill()
            server.wait(timeout=30)
    warm = [r["secs"] for r in rows if not r["first"] and r["secs"] > 0]
    per_kind: dict[str, list[int]] = {}
    for r in rows:
        per_kind.setdefault(r["kind"], [0, 0])
        per_kind[r["kind"]][0] += r["hit"]
        per_kind[r["kind"]][1] += 1
    result.update({
        "status": "MEASURED" if "stopped" not in result else "STOPPED",
        "targets": len(rows), "hits": sum(r["hit"] for r in rows),
        "per_kind": {k: f"{h}/{n}" for k, (h, n) in per_kind.items()},
        "no_answer": sum(r["point"] is None for r in rows),
        "cold_first_secs": rows[0]["secs"] if rows else None,
        "warm_median_secs": round(statistics.median(warm), 2) if warm else None,
        "warm_p90_secs": (round(sorted(warm)[int(len(warm) * 0.9) - 1], 2) if warm else None),
        "ollama_size_vram_mib": round(vram / 2**20) if vram else None,
        "gpu_peak_delta_mib": peak["gpu"] - base if base >= 0 else None,
        "cpu_peak_percent": peak["cpu"], "ram_free_min_mib": peak["ram_free_min"],
        "median_output_tokens": statistics.median([r["tokens"] for r in rows]) if rows else None,
        "rows": rows})
    (d / f"result-{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=1))


def restore_default() -> None:
    """Nova keeps its default model loaded (models.yaml keep_default_loaded)."""
    _unload_all()
    httpx.post(f"{OLLAMA}/api/generate", json={"model": "qwen3:8b", "keep_alive": -1},
               timeout=300)


def report() -> None:
    d = bench_dir()
    print(f"{'model':16} {'status':10} {'acc':>9} {'cold s':>7} {'warm s':>7} {'p90 s':>6} "
          f"{'GPU+ MiB':>9}  per kind")
    for f in sorted(d.glob("result-*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        if r["status"] not in ("MEASURED", "STOPPED"):
            print(f"{r['model']:16} {r['status']:10} {r.get('reason', '')}")
            continue
        print(f"{r['model']:16} {r['status']:10} {r['hits']:>4}/{r['targets']:<4} "
              f"{r['cold_first_secs']:>7} {r['warm_median_secs']:>7} {r['warm_p90_secs']:>6} "
              f"{r['gpu_peak_delta_mib']:>9}  {r['per_kind']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")           # type: ignore[union-attr]
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "collect":
        collect()
    elif cmd == "run":
        try:
            run(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        finally:
            restore_default()
    else:
        report()
