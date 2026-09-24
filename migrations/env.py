from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from core.db.models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,          # SQLite needs batch mode for ALTER
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:        # handed over by core.db.migrate
        _run(connection)
        return

    # CLI use: resolve the DB from the agent's own config.
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    from core.config import load_config
    from core.db.engine import make_engine

    engine = make_engine(load_config().path("database"))
    with engine.connect() as conn:
        _run(conn)


run_online()
