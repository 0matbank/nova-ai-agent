"""Measure what loading the local model costs in RAM (owner 2026-10-16: RAM keeps
hitting 93–100 %). Compares:

  A  "old"  — warm without options, then a chat with num_ctx (what Nova did)
  B  "new"  — warm and chat with the same options (num_ctx, use_mmap)

For each: peak system RAM during the load, whether the chat reloaded the model
(new runner PID), and the runner's resident / private memory afterwards.

    uv run python scripts/ollama_memory_probe.py [--mode old|new|both]

It unloads and reloads qwen3:8b (a few seconds of slower answers for Nova).
"""

import argparse
import contextlib
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import load_config  # noqa: E402

BASE = "http://127.0.0.1:11434"
GB = 1024 ** 3


def runner() -> psutil.Process | None:
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower().startswith(("llama-server", "ollama_llama_server")):
            return p
    return None


class Sampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak_used = 0.0
        self.peak_private = 0.0
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            vm = psutil.virtual_memory()
            self.peak_used = max(self.peak_used, (vm.total - vm.available) / GB)
            r = runner()
            if r is not None:
                with contextlib.suppress(psutil.Error):
                    self.peak_private = max(self.peak_private, r.memory_info().private / GB)
            time.sleep(0.2)


def state(label: str) -> dict[str, float]:
    vm = psutil.virtual_memory()
    r = runner()
    mi = r.memory_info() if r is not None else None
    out = {"used": (vm.total - vm.available) / GB, "pct": vm.percent,
           "rss": mi.rss / GB if mi else 0.0, "private": mi.private / GB if mi else 0.0,
           "pid": float(r.pid) if r is not None else 0.0}
    print(f"  {label:<28} RAM {out['used']:.2f} GB ({out['pct']:.0f}%) · runner pid "
          f"{int(out['pid'])} resident {out['rss']:.2f} GB private {out['private']:.2f} GB")
    return out


def unload(c: httpx.Client, model: str) -> None:
    c.post(f"{BASE}/api/generate", json={"model": model, "keep_alive": 0})
    for _ in range(50):
        if not c.get(f"{BASE}/api/ps").json().get("models"):
            return
        time.sleep(0.2)


def run(mode: str, model: str, num_ctx: int) -> None:
    print(f"\n=== mode {mode} ===")
    opts = {"num_ctx": num_ctx}
    if mode == "new":
        opts["use_mmap"] = True
    with httpx.Client(timeout=300) as c:
        unload(c, model)
        time.sleep(2)
        state("unloaded")
        s = Sampler()
        s.start()
        warm = {"model": model, "keep_alive": -1}
        if mode == "new":
            warm["options"] = opts
        t0 = time.time()
        c.post(f"{BASE}/api/generate", json=warm)
        after_warm = state(f"after warm ({time.time() - t0:.1f}s)")
        t0 = time.time()
        c.post(f"{BASE}/api/chat", json={
            "model": model, "stream": False, "think": False, "keep_alive": -1,
            "messages": [{"role": "user", "content": "Say OK."}],
            "options": {"num_predict": 5, **opts}})
        after_chat = state(f"after first chat ({time.time() - t0:.1f}s)")
        time.sleep(3)
        s.stop.set()
        s.join()
        settled = state("settled 3 s later")
        reloaded = after_warm["pid"] != after_chat["pid"]
        print(f"  peak RAM during load+chat: {s.peak_used:.2f} GB · runner peak private "
              f"{s.peak_private:.2f} GB · chat reloaded the model: {reloaded}")
        ps = c.get(f"{BASE}/api/ps").json().get("models", [])
        for m in ps:
            print(f"  loaded: {m['name']} ctx {m.get('context_length')} "
                  f"vram {m.get('size_vram', 0) / GB:.2f} GB")
        print(f"  settled private {settled['private']:.2f} GB")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["old", "new", "both"], default="both")
    args = ap.parse_args()
    cfg = load_config()
    model = str(cfg.models.alias_sets["ollama_models"][cfg.models.ollama_policy.default_alias])
    modes = ["old", "new"] if args.mode == "both" else [args.mode]
    for m in modes:
        run(m, model, cfg.models.ollama_policy.num_ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
