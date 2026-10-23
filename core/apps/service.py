"""Wiring for the app resolver (V1.1): catalog file in the data folder, learned aliases
in the agent DB (`memories`, layer long_term, kind app_alias)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from core.apps.catalog import Catalog
from core.apps.resolver import AppResolver, MemoryAliases, safe_alias
from core.config.schema import AppConfig
from core.db.models import Memory, utcnow

LAYER, KIND = "long_term", "app_alias"


class DbAliases:
    """The owner's corrections, kept across restarts. Only short plain names that map
    to an app id — never credentials (safe_alias)."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def all(self) -> dict[str, str]:
        with self.sessions() as s:
            rows = s.scalars(select(Memory).where(Memory.layer == LAYER,
                                                  Memory.kind == KIND)).all()
            return {r.key: r.content for r in rows}

    def learn(self, phrase: str, app_id: str, source: str) -> bool:
        if not safe_alias(phrase):
            return False
        with self.sessions() as s:
            row = s.scalar(select(Memory).where(Memory.layer == LAYER, Memory.kind == KIND,
                                                Memory.key == phrase[:200]))
            if row is None:
                s.add(Memory(layer=LAYER, kind=KIND, key=phrase[:200], content=app_id,
                             source=source))
            else:
                row.content, row.source, row.updated_at = app_id, source, utcnow()
            s.commit()
        return True


def catalog_path(cfg: AppConfig) -> Any:
    return cfg.path("data_dir") / "app_catalog.json"


def build_app_resolver(cfg: AppConfig, sessions: sessionmaker[Session] | None = None,
                       scan_if_missing: bool = False) -> AppResolver:
    res = AppResolver(catalog_path(cfg),
                      DbAliases(sessions) if sessions is not None else MemoryAliases())
    if scan_if_missing and Catalog.load(res.catalog_path) is None:
        res.rescan()
    return res
