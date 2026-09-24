"""Permission Engine (plan §18).

GREEN  = automatic
BLUE   = automatic + log
YELLOW = safety check; if the check fails or none exists → needs approval
RED    = explicit approval (single task/action, never reused)

Unknown actions fall back to `default_level` (RED — fail closed). While
/lockdown is active everything above GREEN is denied (plan §27).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session, sessionmaker

from core.config.schema import Level, PermissionsConfig
from core.db.models import Setting

LOCKDOWN_KEY = "security.lockdown"


class Decision(StrEnum):
    ALLOW = "allow"                    # GREEN
    ALLOW_LOGGED = "allow_logged"      # BLUE
    NEEDS_CHECK = "needs_check"        # YELLOW
    NEEDS_APPROVAL = "needs_approval"  # RED
    DENY = "deny"                      # lockdown


@dataclass(frozen=True)
class Evaluation:
    action: str
    level: Level
    decision: Decision
    reason: str


class PermissionDenied(Exception):
    def __init__(self, action: str, reason: str) -> None:
        super().__init__(f"{action}: {reason}")
        self.action = action
        self.reason = reason


class PermissionEngine:
    def __init__(self, config: PermissionsConfig, sessions: sessionmaker[Session]) -> None:
        self.config = config
        self._sessions = sessions

    def level_of(self, action: str) -> Level:
        return self.config.actions.get(action, Level(self.config.default_level))

    def evaluate(self, action: str) -> Evaluation:
        level = self.level_of(action)
        if self.is_lockdown() and level is not Level.GREEN:
            return Evaluation(action, level, Decision.DENY, "lockdown active")
        decision = {
            Level.GREEN: Decision.ALLOW,
            Level.BLUE: Decision.ALLOW_LOGGED,
            Level.YELLOW: Decision.NEEDS_CHECK,
            Level.RED: Decision.NEEDS_APPROVAL,
        }[level]
        known = action in self.config.actions
        return Evaluation(action, level, decision, "configured" if known else "unknown action")

    # ------------------------------------------------------------ lockdown

    def is_lockdown(self) -> bool:
        with self._sessions() as s:
            row = s.get(Setting, LOCKDOWN_KEY)
            return bool(row and row.value)

    def set_lockdown(self, on: bool) -> None:
        with self._sessions.begin() as s:
            row = s.get(Setting, LOCKDOWN_KEY)
            if row is None:
                s.add(Setting(key=LOCKDOWN_KEY, value=on))
            else:
                row.value = on
