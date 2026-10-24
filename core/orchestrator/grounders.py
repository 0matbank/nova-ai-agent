"""Optional visual grounder (plan §72 CU-8 follow-up, owner-approved 2026-10-24):
UI-TARS-1.5-7B through llama.cpp's own server, behind `models.yaml → grounding.engine`.

Default is "vision" — the existing path (qwen3-vl through the provider router) — and
nothing here runs. With "ui-tars", `locate` asks UI-TARS first; no answer or any error
falls back to the existing path. The server starts on demand, only after Nova's Ollama
models are unloaded (one model on the GPU at a time — owner rule), and stops when idle.
Screenshots stay on this PC (a local process on 127.0.0.1)."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from core.log import get_logger

_log = get_logger("core")

PROMPT = ("Output only the coordinate of one point in your response. "
          "What element matches the following task: click {target}")
OLLAMA = "http://127.0.0.1:11434"
START_SECONDS = 120


def point_from_answer(answer: str, size: tuple[int, int]) -> tuple[float, float] | None:
    """UI-TARS answers '(x,y)' (or a box) in the image's own pixels under llama.cpp
    (calibrated 2026-10-24 on synthetic buttons). None when it gives no point inside."""
    nums = re.findall(r"-?\d+(?:\.\d+)?", answer)
    if len(nums) < 2:
        return None
    x, y = float(nums[0]), float(nums[1])
    if len(nums) >= 4:
        x, y = (x + float(nums[2])) / 2, (y + float(nums[3])) / 2
    w, h = size
    if not (0 <= x < w and 0 <= y < h):
        return None
    return x, y


class UiTarsGrounder:
    def __init__(self, root: Path, cfg: Any) -> None:
        self.server = root / cfg.server
        self.model = root / cfg.model
        self.mmproj = root / cfg.mmproj
        self.url = f"http://127.0.0.1:{cfg.port}"
        self.idle = cfg.idle_unload_seconds
        self._proc: subprocess.Popen[bytes] | None = None
        self._last = 0.0
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None

    def available(self) -> bool:
        return all(p.is_file() for p in (self.server, self.model, self.mmproj))

    async def _unload_ollama(self) -> None:
        async with httpx.AsyncClient(timeout=30) as c:
            with contextlib.suppress(httpx.HTTPError):
                for m in (await c.get(f"{OLLAMA}/api/ps")).json().get("models", []):
                    await c.post(f"{OLLAMA}/api/generate",
                                 json={"model": m["name"], "keep_alive": 0})

    async def _ensure(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        await self._unload_ollama()                     # one model on the GPU at a time
        self._proc = subprocess.Popen(  # noqa: S603 - a fixed local binary from config
            [str(self.server), "-m", str(self.model), "--mmproj", str(self.mmproj),
             "-ngl", "99", "-c", "8192", "--host", "127.0.0.1",
             "--port", self.url.rsplit(":", 1)[1], "--no-webui", "--load-mode", "none"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + START_SECONDS
        async with httpx.AsyncClient(timeout=2) as c:
            while time.monotonic() < deadline:
                with contextlib.suppress(httpx.HTTPError):
                    if (await c.get(f"{self.url}/health")).status_code == 200:
                        break
                if self._proc.poll() is not None:
                    raise RuntimeError("ui-tars server exited while starting")
                await asyncio.sleep(1)
            else:
                self.stop()
                raise RuntimeError("ui-tars server did not start")
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())

    async def _reap(self) -> None:
        while self._proc is not None and self._proc.poll() is None:
            await asyncio.sleep(10)
            if time.monotonic() - self._last > self.idle:
                async with self._lock:
                    if time.monotonic() - self._last > self.idle:
                        self.stop()

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._proc.wait(timeout=15)
        self._proc = None

    async def locate(self, png: bytes, size: tuple[int, int], target: str
                     ) -> tuple[float, float] | None:
        """A point in image pixels, or None (the caller falls back)."""
        if not self.available():
            return None
        async with self._lock:
            try:
                await self._ensure()
                self._last = time.monotonic()
                uri = "data:image/png;base64," + base64.b64encode(png).decode()
                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.post(f"{self.url}/v1/chat/completions", json={
                        "temperature": 0, "max_tokens": 64, "messages": [{
                            "role": "user", "content": [
                                {"type": "image_url", "image_url": {"url": uri}},
                                {"type": "text", "text": PROMPT.format(target=target[:200])}]}]})
                r.raise_for_status()
                answer = r.json()["choices"][0]["message"]["content"]
            except (httpx.HTTPError, RuntimeError, KeyError, ValueError, OSError) as e:
                _log.warning(f"ui-tars locate failed, falling back: {e}",
                             extra={"action": "grounding.ui_tars", "status": "fallback"})
                return None
            finally:
                self._last = time.monotonic()
        return point_from_answer(str(answer), size)


_CACHE: dict[str, UiTarsGrounder] = {}


def grounder_for(config: Any) -> UiTarsGrounder | None:
    """The configured optional grounder, or None (default: the existing vision path)."""
    g = config.models.grounding
    if g.engine != "ui-tars":
        return None
    key = str(config.root)
    if key not in _CACHE:
        _CACHE[key] = UiTarsGrounder(config.root, g.ui_tars)
    return _CACHE[key]
