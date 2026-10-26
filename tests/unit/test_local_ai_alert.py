"""Owner 2026-10-26: Ollama had been closed for a day and Nova silently answered from the
cloud. Down two checks in a row → the owner is told once; back → told once."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from core.service import LOCAL_AI_BACK, LOCAL_AI_DOWN, _provider_health_loop


@dataclass
class H:
    state: str
    detail: str = ""


class Providers:
    def __init__(self, states: list[str], stop: asyncio.Event) -> None:
        self.states, self.stop = states, stop

    async def check_all(self) -> dict[str, H]:
        if not self.states:
            self.stop.set()
            return {}
        return {"ollama_local": H(self.states.pop(0)), "gemini_api": H("HEALTHY")}


def test_told_once_when_down_and_once_when_back() -> None:
    told: list[str] = []

    async def notify(text: str) -> None:
        told.append(text)

    async def go() -> None:
        stop = asyncio.Event()
        states = ["HEALTHY", "UNAVAILABLE", "HEALTHY",          # one blip: nothing said
                  "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE",  # down: said once
                  "HEALTHY", "HEALTHY"]                         # back: said once
        await _provider_health_loop(Providers(states, stop), stop,  # type: ignore[arg-type]
                                    every=0, notify=notify)
    asyncio.run(go())
    assert told == [LOCAL_AI_DOWN, LOCAL_AI_BACK]
