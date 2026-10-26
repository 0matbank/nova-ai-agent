"""Context Manager (plan §49.2, §58): build a small, task-relevant context
package — never the full conversation. Sections with nothing to say are
left out. Project memory / diffs / file selection plug in from later phases."""

from __future__ import annotations

from dataclasses import dataclass, field

SECTION_ORDER = ("Task Goal", "Relevant User Instruction", "Recent Conversation",
                 "Project Rules", "Relevant Memory",
                 "Selected Files", "Current Diff", "Previous Decisions", "Current Checkpoint",
                 "Known Errors", "Allowed Skills", "Risk Level")
MAX_SECTION_CHARS = 2000
# V1.2 (owner 2026-10-26, idea from Atticus, MIT): follow-ups ("read me what you said",
# "why did you stop", "the second one") need the last answer itself
SECTION_LIMITS = {"Recent Conversation": 6500}
LAST_ANSWER_CHARS, EARLIER_ANSWER_CHARS = 4000, 1000


@dataclass
class ContextPackage:
    sections: dict[str, str] = field(default_factory=dict)

    def add(self, name: str, text: str | None) -> ContextPackage:
        if name not in SECTION_ORDER:
            raise ValueError(f"unknown context section {name!r}")
        if text and text.strip():
            self.sections[name] = text.strip()[:SECTION_LIMITS.get(name, MAX_SECTION_CHARS)]
        return self

    def render(self) -> str:
        return "\n\n".join(f"## {n}\n{self.sections[n]}" for n in SECTION_ORDER
                           if n in self.sections)


def conversation(turns: list[tuple[str, str]]) -> str:
    """Earlier asks and answers, oldest first — the last answer whole (up to 4000)."""
    if not turns:
        return ""
    lines = ["Earlier in this chat (only to understand follow-ups such as 'that', 'it', "
             "'read it out', 'say it again'; it is not a new request):"]
    for i, (asked, answered) in enumerate(turns):
        limit = LAST_ANSWER_CHARS if i == len(turns) - 1 else EARLIER_ANSWER_CHARS
        lines.append(f"Owner: {asked[:500]}\nNova: {answered[:limit]}")
    return "\n".join(lines)


def for_request(goal: str, instruction: str, risk: str = "GREEN",
                allowed_skills: list[str] | None = None, checkpoint: str | None = None,
                known_errors: str | None = None) -> ContextPackage:
    pkg = ContextPackage()
    pkg.add("Task Goal", goal)
    pkg.add("Relevant User Instruction", instruction)
    pkg.add("Current Checkpoint", checkpoint)
    pkg.add("Known Errors", known_errors)
    pkg.add("Allowed Skills", ", ".join(allowed_skills or []))
    pkg.add("Risk Level", risk)
    return pkg
