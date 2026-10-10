"""
Schema management at start-up.

SQLite (tests, local dev): create_all - throwaway databases.
Everything else (PostgreSQL in production): the schema is owned by Alembic
and this only VERIFIES it. If the database is not at the migration head the
application refuses to start with an explicit instruction, instead of
running new code against an old schema. On Render the previous deploy keeps
serving until the migration has been run.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from .database import create_all_tables, engine

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def head_revision() -> str:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def ensure_schema() -> None:
    if engine.dialect.name == "sqlite":
        create_all_tables()
        return

    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
        has_tables = bool(set(inspect(conn).get_table_names()) - {"alembic_version"})
    head = head_revision()
    if current == head:
        return
    if current is None and has_tables:
        hint = "tables exist but the database was never stamped: run `alembic stamp 0001` first, then `alembic upgrade head`"
    elif current is None:
        hint = "the database is empty: run `alembic upgrade head`"
    else:
        hint = "run `alembic upgrade head`"
    raise RuntimeError(
        f"Database schema is at revision {current!r} but this code expects {head!r} - {hint} "
        "(from apps/backend/src, with RECONFLOW_DATABASE_URL set). See docs/BATCH1_RUNBOOK.md."
    )
