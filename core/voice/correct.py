"""Suggest what an unclear voice command most likely was (local AI).
Only a SUGGESTION shown to the owner — it never runs anything by itself."""

from __future__ import annotations

from typing import Any

from providers.provider_base import Limits, ProviderRequest

SYSTEM = (
    "You fix speech-recognition mistakes. The text is a short spoken command or question "
    "to a personal PC assistant, in Bangla, English or Banglish. Some words may be "
    "misheard (for example 'পিছি' means 'পিসি', 'ওস্তা' means 'অবস্থা', 'skin shot' means "
    "'screenshot'). Reply with ONLY the corrected sentence, in the same language and "
    "script, and nothing else. NEVER translate: English stays English, Bangla stays "
    "Bangla. If it already looks right, reply with it unchanged."
)


def _clean(answer: str) -> str:
    line = answer.strip().splitlines()[0] if answer.strip() else ""
    return line.strip().strip('"\'«»“”').strip()


async def suggest(router: Any, heard: str, language: str,
                  vocabulary: list[str] | None = None, think: bool = False) -> str | None:
    # Measured 2026-09-24: with the owner's vocabulary and no thinking, qwen3:8b fixed
    # 4/4 misheard Bangla/English commands in ~0.3 s; thinking was slower and worse.
    system = SYSTEM
    if vocabulary:
        system += " Words the owner often uses: " + ", ".join(vocabulary) + "."
    task_type = ("bangla" if language == "bn" else "reasoning") if think else "simple"
    result = await router.complete(ProviderRequest(
        task_id="voice", task_type=task_type, user_request=heard, system=system,
        limits=Limits(timeout_seconds=45, max_output_tokens=120)))
    if not result.ok:
        return None
    text = _clean(result.answer)
    if not text or len(text) > max(3 * len(heard), 40):
        return None
    return text
