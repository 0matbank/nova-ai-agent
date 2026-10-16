"""Does the local model's RAM grow with every new prompt? (owner 2026-10-16: RAM
93–100 %). Sends N different long prompts — like Nova's planner / agent prompts —
and prints the model runner's resident memory after each.

llama.cpp's server keeps a prompt cache in RAM (--cache-ram, default 8 GiB) and
Ollama does not set it, so with the model kept loaded it can only grow. The cap
comes from the LLAMA_ARG_CACHE_RAM environment variable of the Ollama server.

    uv run python scripts/ollama_cache_probe.py [--prompts 12]
"""

import argparse
import random
import sys
import time

import httpx
import psutil

BASE = "http://127.0.0.1:11434"
WORDS = ["file", "folder", "drive", "status", "process", "window", "browser", "answer",
         "tool", "result", "project", "test", "commit", "branch", "secret", "memory",
         "skill", "agent", "planner", "step", "owner", "request"]


def runner_rss() -> float:
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower().startswith("llama-server"):
            return p.memory_info().rss / 1024 ** 3
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", type=int, default=12)
    ap.add_argument("--model", default="qwen3:8b")
    args = ap.parse_args()
    rnd = random.Random(16)          # noqa: S311 - filler text, not security
    start = runner_rss()
    print(f"start: runner resident {start:.2f} GB")
    with httpx.Client(timeout=300) as c:
        for i in range(1, args.prompts + 1):
            text = " ".join(rnd.choice(WORDS) for _ in range(2500))     # ~3k tokens, unique
            t = time.time()
            c.post(f"{BASE}/api/chat", json={
                "model": args.model, "stream": False, "think": False, "keep_alive": -1,
                "messages": [{"role": "system", "content": f"Request {i}. Reply OK."},
                             {"role": "user", "content": text}],
                "options": {"num_predict": 3, "num_ctx": 16384}})
            print(f"prompt {i:>2}: {time.time() - t:4.1f}s · runner resident {runner_rss():.2f} GB")
    end = runner_rss()
    print(f"growth: {end - start:+.2f} GB over {args.prompts} prompts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
