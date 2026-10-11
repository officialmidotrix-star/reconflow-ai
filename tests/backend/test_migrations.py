"""
Alembic migrations, tested against real databases.

Always runs on SQLite. Also runs on PostgreSQL - the production target, where
enum types and transactional DDL behave differently - when RECONFLOW_TEST_PG_URL
is set to a server where the test role may CREATE DATABASE, e.g.

    RECONFLOW_TEST_PG_URL=postgresql://user:pw@127.0.0.1:5432/postgres pytest tests/backend/test_migrations.py

The "existing database" tests build the PRE-tenancy schema by upgrading to the
frozen baseline 0001 (exactly what production's create_all produced), insert a
single-organization world with plain SQL, then run the real migration.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

SRC = Path(__file__).resolve().parents[2] / "apps" / "backend" / "src"
PG_URL = os.environ.get("RECONFLOW_TEST_PG_URL")


def _alembic_cfg(x: dict | None = None) -> Config:
    cfg = Config(str(SRC / "alembic.ini"))
    cfg.set_main_option("script_location", str(SRC / "migrations"))
    if x:
        cfg.cmd_opts = type("O", (), {"x": [f"{k}={v}" for k, v in x.items()]})()
    return cfg


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.skipif(not PG_URL, reason="RECONFLOW_TEST_PG_URL not set"))])
def db_url(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path}/m.db"
        monkeypatch.setenv("RECONFLOW_DATABASE_URL", url)
        yield url
        return
    from bootstrap.config import normalize_database_url

    base = make_url(normalize_database_url(PG_URL))
    name = f"rf_mig_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as a:
        a.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("RECONFLOW_DATABASE_URL", url)
    yield url
    with admin.connect() as a:
        a.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _engine(url: str):
    from bootstrap.config import normalize_database_url

    return create_engine(normalize_database_url(url))


def _old_world(url: str, *, orgs: int = 1, with_operator_candidate: bool = True) -> dict:
    """Upgrade to the baseline and insert a pre-tenancy world with plain SQL."""
    command.upgrade(_alembic_cfg(), "0001")
    e = _engine(url)
    ids = {"orgs": [f"org-{i}" for i in range(orgs)], "branch": "br-1", "analysis": "an-1"}
    now = "2026-03-01 10:00:00"
    with e.begin() as c:
        for oid in ids["orgs"]:
            c.execute(text("INSERT INTO organizations (id, legal_name, default_currency, created_at) VALUES (:i, :n, 'SAR', :t)"),
                      {"i": oid, "n": f"Org {oid}", "t": now})
        for uid, email, role in (("u-owner", "owner@x.test", "OWNER"), ("u-acct", "acct@x.test", "ACCOUNTANT")):
            c.execute(text("INSERT INTO users (id, email, password_hash, role, is_active, created_at) "
                           "VALUES (:i, :e, 'hash', :r, true, :t)"), {"i": uid, "e": email, "r": role, "t": now})
        c.execute(text("INSERT INTO branches (id, organization_id, name, timezone, created_at) VALUES ('br-1', :o, 'B', 'Asia/Riyadh', :t)"),
                  {"o": ids["orgs"][0], "t": now})
        c.execute(text(
            "INSERT INTO analyses (id, branch_id, created_by, version, status, period_start, period_end, created_at, updated_at) "
            "VALUES ('an-1', 'br-1', 'u-owner', 1, 'COMPLETED', '2026-03-01', '2026-03-31', :t, :t)"), {"t": now})
        for eid, event, uid, aid in (
            ("a1", "analysis_created", "u-acct", "an-1"),   # attributable via analysis -> branch -> org
            ("a2", "user_created", "u-acct", None),          # attributable via the acting user
            ("a3", "organization_created", "u-owner", None),  # actor becomes the operator -> no client org
        ):
            c.execute(text("INSERT INTO audit_log_entries (id, event, user_id, analysis_id, details, created_at) "
                           "VALUES (:i, :ev, :u, :a, '{}', :t)"), {"i": eid, "ev": event, "u": uid, "a": aid, "t": now})
    e.dispose()
    return ids


def _head() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_cfg()).get_current_head()


def _rows(url: str, sql: str):
    e = _engine(url)
    with e.connect() as c:
        rows = c.execute(text(sql)).fetchall()
    e.dispose()
    return rows


# -- fresh database ---------------------------------------------------------


class TestFreshDatabase:
    def test_upgrade_head_creates_exactly_the_model_schema(self, db_url):
        command.upgrade(_alembic_cfg(), "head")
        command.check(_alembic_cfg())  # raises if models and migrations disagree

        from bootstrap.model_registry import import_all_models
        from db.base import Base

        import_all_models()
        insp = inspect(_engine(db_url))
        assert set(insp.get_table_names()) - {"alembic_version"} == set(Base.metadata.tables)
        user_cols = {c["name"] for c in insp.get_columns("users")}
        assert "organization_id" in user_cols
        assert "organization_id" in {c["name"] for c in inspect(_engine(db_url)).get_columns("audit_log_entries")}

    def test_fresh_database_needs_no_operator_argument(self, db_url):
        command.upgrade(_alembic_cfg(), "head")  # no users -> nothing to promote, no refusal
        assert _rows(db_url, "SELECT version_num FROM alembic_version") == [(_head(),)]

    def test_check_constraint_rejects_a_client_operator_and_an_org_less_client(self, db_url):
        command.upgrade(_alembic_cfg(), "head")
        e = _engine(db_url)
        with e.begin() as c:
            c.execute(text("INSERT INTO organizations (id, legal_name, default_currency, created_at) VALUES ('o', 'O', 'SAR', '2026-01-01 00:00:00')"))
        for role, org in (("PLATFORM_OPERATOR", "o"), ("OWNER", None)):
            with pytest.raises(IntegrityError):
                with e.begin() as c:
                    c.execute(text("INSERT INTO users (id, email, password_hash, role, organization_id, is_active, created_at) "
                                   "VALUES (:i, :e, 'h', :r, :o, true, '2026-01-01 00:00:00')"),
                              {"i": f"u-{role}", "e": f"{role}@x", "r": role, "o": org})
        with e.begin() as c:  # the legal combinations are accepted
            c.execute(text("INSERT INTO users (id, email, password_hash, role, organization_id, is_active, created_at) "
                           "VALUES ('op', 'op@x', 'h', 'PLATFORM_OPERATOR', NULL, true, '2026-01-01 00:00:00')"))
            c.execute(text("INSERT INTO users (id, email, password_hash, role, organization_id, is_active, created_at) "
                           "VALUES ('ow', 'ow@x', 'h', 'OWNER', 'o', true, '2026-01-01 00:00:00')"))
        e.dispose()


# -- existing single-organization database ---------------------------------------


class TestExistingDatabase:
    def test_upgrade_assigns_org_promotes_the_named_operator_and_attributes_audit(self, db_url):
        ids = _old_world(db_url)
        command.upgrade(_alembic_cfg({"operator_email": "owner@x.test"}), "head")

        users = {r[0]: (r[1], r[2]) for r in _rows(db_url, "SELECT email, CAST(role AS VARCHAR), organization_id FROM users")}
        assert users["owner@x.test"] == ("PLATFORM_OPERATOR", None)
        assert users["acct@x.test"] == ("ACCOUNTANT", ids["orgs"][0])

        audit = dict(_rows(db_url, "SELECT id, organization_id FROM audit_log_entries"))
        assert audit["a1"] == ids["orgs"][0]  # via analysis -> branch -> org
        assert audit["a2"] == ids["orgs"][0]  # via the acting user's organization
        assert audit["a3"] is None            # actor became the operator: operator-only

        # nothing else was touched
        assert _rows(db_url, "SELECT count(*) FROM analyses") == [(1,)]
        assert _rows(db_url, "SELECT count(*) FROM branches") == [(1,)]

    def test_refuses_without_an_operator_rather_than_locking_everyone_out(self, db_url):
        _old_world(db_url)
        with pytest.raises(Exception) as exc:
            command.upgrade(_alembic_cfg(), "head")
        assert "no platform operator" in str(exc.value)
        # refused BEFORE changing anything
        assert _rows(db_url, "SELECT version_num FROM alembic_version") == [("0001",)]
        assert "organization_id" not in {c["name"] for c in inspect(_engine(db_url)).get_columns("users")}

    def test_refuses_an_operator_email_that_matches_nobody(self, db_url):
        _old_world(db_url)
        with pytest.raises(Exception) as exc:
            command.upgrade(_alembic_cfg({"operator_email": "typo@x.test"}), "head")
        assert "matches no existing user" in str(exc.value)
        assert _rows(db_url, "SELECT version_num FROM alembic_version") == [("0001",)]

    def test_refuses_to_guess_between_several_organizations(self, db_url):
        _old_world(db_url, orgs=2)
        with pytest.raises(Exception) as exc:
            command.upgrade(_alembic_cfg({"operator_email": "owner@x.test"}), "head")
        assert "choose one" in str(exc.value)
        assert _rows(db_url, "SELECT version_num FROM alembic_version") == [("0001",)]

    def test_org_id_argument_resolves_the_ambiguity(self, db_url):
        ids = _old_world(db_url, orgs=2)
        command.upgrade(_alembic_cfg({"operator_email": "owner@x.test", "org_id": ids["orgs"][1]}), "head")
        assert _rows(db_url, "SELECT organization_id FROM users WHERE email='acct@x.test'") == [(ids["orgs"][1],)]

    def test_downgrade_restores_the_old_shape_and_demotes_the_operator(self, db_url):
        _old_world(db_url)
        command.upgrade(_alembic_cfg({"operator_email": "owner@x.test"}), "head")
        command.downgrade(_alembic_cfg(), "0001")
        cols = {c["name"] for c in inspect(_engine(db_url)).get_columns("users")}
        assert "organization_id" not in cols
        roles = dict(_rows(db_url, "SELECT email, CAST(role AS VARCHAR) FROM users"))
        assert roles == {"owner@x.test": "OWNER", "acct@x.test": "ACCOUNTANT"}
        assert _rows(db_url, "SELECT count(*) FROM audit_log_entries") == [(3,)]
        # and it can go forward again
        command.upgrade(_alembic_cfg({"operator_email": "owner@x.test"}), "head")
        assert _rows(db_url, "SELECT version_num FROM alembic_version") == [(_head(),)]

    def test_numbers_migration_labels_old_discrepancies_honestly_and_keeps_the_data(self, db_url):
        """0003: rows written before the new rules are never guessed at."""
        _old_world(db_url)
        command.upgrade(_alembic_cfg({"operator_email": "owner@x.test"}), "0002")
        e = _engine(db_url)
        t = "2026-03-01 10:00:00"
        with e.begin() as c:
            c.execute(text("INSERT INTO reconciliation_matches (id, analysis_id, created_at) VALUES ('m1','an-1',:t)"), {"t": t})
            for did, cat in (("d1", "MISSING_SETTLEMENT"), ("d2", "UNEXPECTED_SETTLEMENT"),
                             ("d3", "CANCELLED_AFTER_PREPARATION"), ("d4", "SETTLEMENT_AMOUNT_MISMATCH")):
                c.execute(text("INSERT INTO discrepancies (id, analysis_id, reconciliation_match_id, category, severity, estimated_loss, created_at) "
                               "VALUES (:i,'an-1','m1',:c,'LOW',12.50,:t)"), {"i": did, "c": cat, "t": t})
            c.execute(text("INSERT INTO discrepancy_runs (id, analysis_id, status, total_count, critical_count, high_count, medium_count, low_count, checked_at) "
                           "VALUES ('r1','an-1','COMPLETED',4,0,0,0,4,:t)"), {"t": t})
        e.dispose()

        command.upgrade(_alembic_cfg(), "head")
        rows = {r[0]: r[1:] for r in _rows(db_url, "SELECT id, treatment, direction, claimable_amount, order_reference, estimated_loss FROM discrepancies")}
        assert rows["d1"][0] == "NEEDS_RERUN" and rows["d4"][0] == "NEEDS_RERUN"
        assert rows["d2"][0] == "TO_VERIFY" and rows["d3"][0] == "NEEDS_CONFIRMATION"
        assert all(v[1] is None and v[2] is None and v[3] is None for v in rows.values())  # nothing invented
        assert all(float(v[4]) == 12.5 for v in rows.values())                              # amounts untouched
        assert _rows(db_url, "SELECT excluded_refund_void_count FROM discrepancy_runs") == [(0,)]
        assert _rows(db_url, "SELECT count(*) FROM cancellation_confirmations") == [(0,)]
        # and it reverses cleanly
        command.downgrade(_alembic_cfg(), "0002")
        assert "treatment" not in {c["name"] for c in inspect(_engine(db_url)).get_columns("discrepancies")}
        assert _rows(db_url, "SELECT count(*) FROM discrepancies") == [(4,)]

    def test_the_baseline_itself_can_never_be_downgraded(self, db_url):
        command.upgrade(_alembic_cfg(), "0001")
        with pytest.raises(NotImplementedError):
            command.downgrade(_alembic_cfg(), "base")


# -- supporting guarantees ---------------------------------------------------------


class TestSupport:
    def test_model_registry_covers_every_table_the_full_app_registers(self):
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from db.base import Base\n"
            "%s\n"
            "print(','.join(sorted(Base.metadata.tables)))"
        )
        reg = subprocess.run(
            [sys.executable, "-c", code % (str(SRC), "from bootstrap.model_registry import import_all_models; import_all_models()")],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        full = subprocess.run(
            [sys.executable, "-c", code % (str(SRC), "import bootstrap.wiring")],
            capture_output=True, text=True, check=True,
            env=dict(os.environ, RECONFLOW_DATABASE_URL="sqlite://", RECONFLOW_ENCRYPTION_KEY="rXq2m6mO4m4g4q3Qz0m8Kx3l7Zr8mC1b9mN0oP5qTjE="),
        ).stdout.strip().splitlines()[-1]
        assert reg == full, "bootstrap/model_registry.py is missing a module's models"

    @pytest.mark.parametrize("given,expected", [
        ("postgresql://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgres://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgresql+psycopg2://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ])
    def test_database_url_is_pinned_to_the_shipped_driver(self, given, expected):
        from bootstrap.config import normalize_database_url

        assert normalize_database_url(given) == expected

    def test_app_refuses_to_start_on_a_postgres_database_that_is_not_at_head(self, monkeypatch):
        """ensure_schema() on a non-SQLite engine must verify the revision, not create tables."""
        import bootstrap.schema as schema

        class _Dialect:
            name = "postgresql"

        class _Engine:
            dialect = _Dialect()

            def connect(self):
                raise RuntimeError("unreachable in this test")

        monkeypatch.setattr(schema, "engine", _Engine())
        with pytest.raises(RuntimeError):
            schema.ensure_schema()
