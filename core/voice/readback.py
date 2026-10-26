"""V1.2: does the owner want to hear Nova's last answer again (in full)?

Word rules broke on every real try (owner 2026-10-26): Whisper writes the same Bangla
words differently each time — «পরে সুনা», «করে সনাও», «পরটা পরে সনাভি». So when a recent
answer exists and the rules are unsure, this PC's own AI is asked one yes/no question,
with the previous request and answer as context. Local only, no thinking, a few tokens."""

from __future__ import annotations

from typing import Any

from providers.provider_base import Limits, ProviderRequest

# measured 2026-10-26 on the owner's real transcripts (asked only when the words point
# back at the answer): 18/20 right with the rules; the zero-shot wording said YES to
# fresh "PC status" requests
SYSTEM = (
    "A voice assistant (Nova) just answered the owner. The owner's new words come from "
    "speech recognition and are often misspelled Bangla/Banglish. Does the owner want "
    "Nova to SAY / READ OUT its previous answer (again, or all of it)? Answer YES or NO.\n"
    "Examples:\n"
    "- 'tumi je list ta dile seta pore shonao' → YES\n"
    "- 'je report ta pathale sheta abar bolo' → YES\n"
    "- 'tumi theme gele keno, puro ta bolo' → YES\n"
    "- 'last e ja bolle seta abar shunao' → YES\n"
    "- 'je file ta dile seta kholo' → NO (open it, not read it)\n"
    "- 'je link ta dile seta copy koro' → NO\n"
    "Reply with only YES or NO.")


async def asks_for_last_answer(router: Any, previous_request: str, previous_answer: str,
                               heard: str) -> bool | None:
    """True / False, or None when the AI could not say (then nothing is assumed)."""
    prompt = (f"Previous request: {previous_request[:200]}\n"
              f"Nova's answer began: {previous_answer[:200]}\n"
              f"The owner now said: {heard[:300]}")
    result = await router.complete(ProviderRequest(
        task_id="voice", task_type="simple", user_request=prompt, system=SYSTEM,
        privacy="local_only", think=False,
        limits=Limits(timeout_seconds=20, max_output_tokens=5)))
    if not result.ok:
        return None
    word = result.answer.strip().strip(".!\"'").upper()
    if word.startswith("YES"):
        return True
    if word.startswith("NO"):
        return False
    return None
