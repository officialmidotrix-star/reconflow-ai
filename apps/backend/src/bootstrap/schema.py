"""
Schema management at start-up.

SQLite (tests, local dev): create_all - throwaway databases.

PostgreSQL (production): the schema is owned by Alembic. Two modes:

* Default - VERIFY only. If the database is not at the migration head the
  application refuses to start with an explicit instruction. On Render the
  previous deploy keeps serving.

* RECONFLOW_AUTO_MIGRATE=1 - MIGRATE AT DEPLOY TIME, then verify. Needs
  nothing installed anywhere: Render starts the new code, the code brings its
  own database up to date, then serves. Guarded:
    - one instance at a time (a PostgreSQL advisory lock);
    - an empty database is simply built from scratch;
    - an existing database that was never migrated is first compared with
      the baseline schema (scratch schema, nothing left behind) and refused
      if it differs;
    - an existing database with users needs RECONFLOW_OPERATOR_EMAIL - the
      account to promote to platform operator, or (if it does not exist yet)
      RECONFLOW_SEED_OPERATOR_PASSWORD so the start-up seed can create it -
      otherwise the app refuses to start rather than risk locking everyone out;
    - any failure aborts the start-up, so the previous deploy keeps serving.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from .baseline_check import baseline_drift
from .config import get_database_url, get_operator_email
from .database import create_all_tables, engine

logger = logging.getLogger("reconflow.bootstrap.schema")

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
ADVISORY_LOCK_KEY = 7263901123  # arbitrary, fixed: "the ReconFlow migration lock"


def _config(x: list[str] | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "migrations"))
    cfg.attributes["skip_logging_config"] = True  # don't reconfigure the app's logging
    if x:
        cfg.cmd_opts = SimpleNamespace(x=x)
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(_config()).get_current_head()


def _state() -> tuple[str | None, bool]:
    """(current alembic revision or None, does the database already have tables)"""
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
        has_tables = bool(set(inspect(conn).get_table_names()) - {"alembic_version"})
    return current, has_tables


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def auto_migrate() -> None:
    url = get_database_url()
    with engine.connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY})
        try:
            current, has_tables = _state()
            head = head_revision()
            if current == head:
                logger.info("Database already at migration head %s.", head)
                return

            x: list[str] = []
            if current is None and not has_tables:
                logger.warning("Empty database: building the schema from scratch (-> %s).", head)
            else:
                x = _operator_arguments()  # refuses BEFORE anything is changed
                if current is None:
                    drift = baseline_drift(engine, url)
                    if drift:
                        raise RuntimeError(
                            "Refusing to migrate: this database's schema differs from the baseline the "
                            "migrations were written for:\n  " + "\n  ".join(drift)
                        )
                    command.stamp(_config(), "0001")
                    logger.warning("Existing database matches the baseline: stamped 0001.")
                logger.warning("Migrating the database to %s ...", head)

            command.upgrade(_config(x), "head")
            logger.warning("Migration complete: database is at %s.", head)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})


def _operator_arguments() -> list[str]:
    """Which account becomes the platform operator when an existing database
    with users is migrated. Refuses (raises) rather than guess."""
    with engine.connect() as conn:
        n_users = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
        if n_users == 0:
            return []
        email = get_operator_email()
        if not email:
            raise RuntimeError(
                "Refusing to migrate: the database has users but RECONFLOW_OPERATOR_EMAIL is not set. "
                "Set it to the email you log in with as the platform admin."
            )
        exists = conn.execute(text("SELECT count(*) FROM users WHERE email = :e"), {"e": email}).scalar_one() > 0
    if exists:
        return [f"operator_email={email}"]
    if os.environ.get("RECONFLOW_SEED_OPERATOR_PASSWORD"):
        return [f"operator_email={email}", "operator_missing_ok=1"]
    raise RuntimeError(
        f"Refusing to migrate: no account {email!r} exists, and RECONFLOW_SEED_OPERATOR_PASSWORD is not set, "
        "so the operator account could not be created either. Set RECONFLOW_SEED_OPERATOR_PASSWORD "
        "(a strong password for that account) or fix RECONFLOW_OPERATOR_EMAIL."
    )


def ensure_schema() -> None:
    if engine.dialect.name == "sqlite":
        create_all_tables()
        return

    if _truthy(os.environ.get("RECONFLOW_AUTO_MIGRATE")):
        auto_migrate()

    current, has_tables = _state()
    head = head_revision()
    if current == head:
        return
    if current is None and has_tables:
        hint = ("tables exist but the database was never migrated: set RECONFLOW_AUTO_MIGRATE=1 "
                "(or run `alembic stamp 0001` then `alembic upgrade head`)")
    elif current is None:
        hint = "the database is empty: set RECONFLOW_AUTO_MIGRATE=1 (or run `alembic upgrade head`)"
    else:
        hint = "set RECONFLOW_AUTO_MIGRATE=1 (or run `alembic upgrade head`)"
    raise RuntimeError(
        f"Database schema is at revision {current!r} but this code expects {head!r} - {hint}. "
        "See docs/runbooks/batch1-1.md."
    )
