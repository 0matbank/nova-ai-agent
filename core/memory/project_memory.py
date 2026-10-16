"""Project memory (plan §20 "Project Memory", §21 write policy, §51, §58; Phase 17):
what Nova learned about a project survives from one task to the next — and a
restart, since it lives in the agent DB (`memories`, layer "project").

Kinds:
  decision     the owner's decision / rule for this project   (owner-written)
  fact         something the owner told Nova to remember       (owner-written)
  known_issue  a problem still open, e.g. failing tests       (Nova, one per topic)
  task         what a finished task on the project did         (Nova, one per task)
  finding      what a review found, e.g. a leaked secret       (Nova, one per task)

Write policy (§21): passwords / tokens / keys are never stored — an entry that
contains one is refused (owner-written) or masked (Nova-written). Temporary
chatter is not stored: only these kinds, written at fixed points, by rules.
Retrieval is project scoped and bounded (§58 rules 3, 13): newest first, a
character budget, decisions always included.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from core.db.models import Memory, Project, utcnow
from core.log import redact

LAYER = "project"
KINDS = ("decision", "fact", "known_issue", "task", "finding")
OWNER_KINDS = ("decision", "fact")
MAX_CONTENT = 1200
KEEP_PER_KIND = {"task": 30, "finding": 20}     # older Nova-written entries roll off


class SecretRefused(ValueError):
    """The text holds a password / token / key — it belongs in the secrets vault."""


@dataclass(frozen=True)
class MemoryEntry:
    id: int
    project: str
    kind: str
    key: str
    content: str
    source: str | None
    task_id: int | None
    updated_at: datetime


class ProjectMemory:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    # ------------------------------------------------------------------ write

    def _project_id(self, s: Session, slug: str, name: str | None = None) -> int:
        p = s.scalar(select(Project).where(Project.slug == slug))
        if p is None:
            p = Project(slug=slug, name=name or slug)
            s.add(p)
            s.flush()
        return p.id

    def put(self, project: str, kind: str, content: str, *, key: str | None = None,
            source: str | None = None, task_id: int | None = None,
            project_name: str | None = None) -> int:
        """Add an entry, or replace the one with the same (project, kind, key) — e.g. one
        `known_issue` per topic, one `task` entry per task id."""
        if kind not in KINDS:
            raise ValueError(f"unknown memory kind {kind!r}")
        text = " ".join(content.split())[:MAX_CONTENT]
        masked = redact(text)
        if masked != text:
            if kind in OWNER_KINDS:
                raise SecretRefused("contains a password / token / key")
            text = masked                       # Nova's own summaries: keep, masked
        if not text:
            raise ValueError("empty memory")
        key = key or f"{kind}:{text[:80].lower()}"
        with self.sessions() as s:
            pid = self._project_id(s, project, project_name)
            row = s.scalar(select(Memory).where(
                Memory.layer == LAYER, Memory.project_id == pid, Memory.kind == kind,
                Memory.key == key[:200]))
            if row is None:
                row = Memory(layer=LAYER, project_id=pid, kind=kind, key=key[:200],
                             content=text, source=source, task_id=task_id)
                s.add(row)
            else:
                row.content, row.source, row.task_id = text, source, task_id
                row.updated_at = utcnow()
            s.flush()
            entry_id = row.id
            self._roll_off(s, pid, kind)
            s.commit()
        return entry_id

    def _roll_off(self, s: Session, pid: int, kind: str) -> None:
        keep = KEEP_PER_KIND.get(kind)
        if keep is None:
            return
        old = list(s.scalars(select(Memory.id).where(
            Memory.layer == LAYER, Memory.project_id == pid, Memory.kind == kind)
            .order_by(Memory.updated_at.desc(), Memory.id.desc()).offset(keep)))
        if old:
            s.execute(delete(Memory).where(Memory.id.in_(old)))

    def resolve(self, project: str, kind: str, key: str) -> bool:
        """An open issue is fixed (e.g. the tests pass again) — drop it."""
        with self.sessions() as s:
            p = s.scalar(select(Project).where(Project.slug == project))
            if p is None:
                return False
            n = s.execute(delete(Memory).where(
                Memory.layer == LAYER, Memory.project_id == p.id, Memory.kind == kind,
                Memory.key == key))
            s.commit()
        return bool(getattr(n, "rowcount", 0))

    def forget(self, project: str, entry_id: int) -> bool:
        with self.sessions() as s:
            p = s.scalar(select(Project).where(Project.slug == project))
            if p is None:
                return False
            n = s.execute(delete(Memory).where(
                Memory.id == entry_id, Memory.layer == LAYER, Memory.project_id == p.id)
            )
            s.commit()
        return bool(getattr(n, "rowcount", 0))

    # ------------------------------------------------------------------- read

    def entries(self, project: str, kinds: tuple[str, ...] = KINDS,
                limit: int = 50) -> list[MemoryEntry]:
        with self.sessions() as s:
            p = s.scalar(select(Project).where(Project.slug == project))
            if p is None:
                return []
            rows = s.scalars(select(Memory).where(
                Memory.layer == LAYER, Memory.project_id == p.id, Memory.kind.in_(kinds))
                .order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(limit))
            return [MemoryEntry(r.id, project, r.kind, r.key, r.content, r.source, r.task_id,
                                r.updated_at) for r in rows]

    def context(self, project: str, budget: int = 1800) -> tuple[str, str]:
        """(relevant memory, previous decisions) for the Context Manager (§49.2): only
        this project's entries, decisions first, newest first, within a budget."""
        decisions = self.entries(project, ("decision",), limit=20)
        rest = self.entries(project, ("known_issue", "fact", "finding", "task"), limit=40)
        dec_lines = [f"- {e.content}" for e in decisions]
        mem_lines = [f"- [{_LABEL[e.kind]} {e.updated_at:%Y-%m-%d}] {e.content}" for e in rest]
        return _fit(mem_lines, budget), _fit(dec_lines, budget // 2)


_LABEL = {"decision": "decision", "fact": "fact", "known_issue": "OPEN ISSUE",
          "task": "earlier task", "finding": "finding"}


def _fit(lines: list[str], budget: int) -> str:
    out: list[str] = []
    used = 0
    for ln in lines:
        if used + len(ln) > budget:
            break
        out.append(ln)
        used += len(ln) + 1
    return "\n".join(out)
