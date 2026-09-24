"""Context Manager (plan §49.2, §58): build a small, task-relevant context
package — never the full conversation. Sections with nothing to say are
left out. Project memory / diffs / file selection plug in from later phases."""

from __future__ import annotations

from dataclasses import dataclass, field

SECTION_ORDER = ("Task Goal", "Relevant User Instruction", "Project Rules", "Relevant Memory",
                 "Selected Files", "Current Diff", "Previous Decisions", "Current Checkpoint",
                 "Known Errors", "Allowed Skills", "Risk Level")
MAX_SECTION_CHARS = 2000


@dataclass
class ContextPackage:
    sections: dict[str, str] = field(default_factory=dict)

    def add(self, name: str, text: str | None) -> ContextPackage:
        if name not in SECTION_ORDER:
            raise ValueError(f"unknown context section {name!r}")
        if text and text.strip():
            self.sections[name] = text.strip()[:MAX_SECTION_CHARS]
        return self

    def render(self) -> str:
        return "\n\n".join(f"## {n}\n{self.sections[n]}" for n in SECTION_ORDER
                           if n in self.sections)


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
