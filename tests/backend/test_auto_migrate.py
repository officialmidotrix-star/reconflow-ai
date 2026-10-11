"""
Deploy-time migration (RECONFLOW_AUTO_MIGRATE=1), on real PostgreSQL.

Skipped unless RECONFLOW_TEST_PG_URL points at a server where the role may
CREATE DATABASE. Each test gets a fresh database and a fresh import of the
application's bootstrap modules, exactly as a Render deploy would.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config

SRC = Path(__file__).resolve().parents[2] / "apps" / "backend" / "src"
PG_URL = os.environ.get("RECONFLOW_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="RECONFLOW_TEST_PG_URL not set")

KEY = "rXq2m6mO4m4g4q3Qz0m8Kx3l7Zr8mC1b9mN0oP5qTjE="


@pytest.fixture()
def pg(monkeypatch, tmp_path):
    from bootstrap.config import normalize_database_url

    base = make_url(normalize_database_url(PG_URL))
    name = f"rf_auto_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as a:
        a.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("RECONFLOW_DATABASE_URL", url)
    monkeypatch.setenv("RECONFLOW_STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("RECONFLOW_ENCRYPTION_KEY", KEY)
    for var in ("RECONFLOW_AUTO_MIGRATE", "RECONFLOW_OPERATOR_EMAIL", "RECONFLOW_SEED_OPERATOR_EMAIL",
                "RECONFLOW_SEED_OPERATOR_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    yield url
    with admin.connect() as a:
        a.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _fresh(modname="bootstrap.schema"):
    for m in list(sys.modules):
        if m == "main" or m.startswith("bootstrap"):
            del sys.modules[m]
    import importlib

    return importlib.import_module(modname)


def _q(url, sql):
    e = create_engine(url)
    with e.connect() as c:
        rows = c.execute(text(sql)).fetchall()
    e.dispose()
    return rows


def _pre_alembic_world(url, *, extra_column: bool = False):
    """The state of today's production: the baseline schema, created without
    Alembic bookkeeping, holding one organization and a few users."""
    cfg = Config(str(SRC / "alembic.ini"))
    cfg.set_main_option("script_location", str(SRC / "migrations"))
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "0001")
    e = create_engine(url)
    with e.begin() as c:
        c.execute(text("DROP TABLE alembic_version"))  # as if create_all had made it
        t = "2026-03-01 10:00:00"
        c.execute(text("INSERT INTO organizations (id, legal_name, default_currency, created_at) VALUES ('o1','Org','SAR',:t)"), {"t": t})
        for uid, email, role in (("u1", "boss@x.test", "OWNER"), ("u2", "n8n@x.test", "OWNER"), ("u3", "acct@x.test", "ACCOUNTANT")):
            c.execute(text("INSERT INTO users (id,email,password_hash,role,is_active,created_at) VALUES (:i,:e,'h',:r,true,:t)"),
                      {"i": uid, "e": email, "r": role, "t": t})
        if extra_column:
            c.execute(text("ALTER TABLE users ADD COLUMN legacy_note text"))
    e.dispose()


def test_empty_database_is_built_from_scratch(pg, monkeypatch):
    monkeypatch.setenv("RECONFLOW_AUTO_MIGRATE", "1")
    _fresh().ensure_schema()
    assert _q(pg, "select version_num from alembic_version") == [(_fresh().head_revision(),)]
    assert len(inspect(create_engine(pg)).get_table_names()) > 25


def test_existing_database_is_stamped_migrated_and_the_named_account_promoted(pg, monkeypatch):
    _pre_alembic_world(pg)
    monkeypatch.setenv("RECONFLOW_AUTO_MIGRATE", "1")
    monkeypatch.setenv("RECONFLOW_OPERATOR_EMAIL", "boss@x.test")
    _fresh().ensure_schema()
    users = dict(_q(pg, "select email, CAST(role AS VARCHAR) from users"))
    assert users == {"boss@x.test": "PLATFORM_OPERATOR", "n8n@x.test": "OWNER", "acct@x.test": "ACCOUNTANT"}
    assert _q(pg, "select count(*) from users where organization_id = 'o1'") == [(2,)]
    # idempotent: a second start changes nothing and does not fail
    _fresh().ensure_schema()
    assert _q(pg, "select count(*) from users where role='PLATFORM_OPERATOR'") == [(1,)]


def test_refuses_without_an_operator_email_and_changes_nothing(pg, monkeypatch):
    _pre_alembic_world(pg)
    monkeypatch.setenv("RECONFLOW_AUTO_MIGRATE", "1")
    with pytest.raises(RuntimeError, match="RECONFLOW_OPERATOR_EMAIL is not set"):
        _fresh().ensure_schema()
    assert "alembic_version" not in inspect(create_engine(pg)).get_table_names()  # not even stamped
    assert "organization_id" not in {c["name"] for c in inspect(create_engine(pg)).get_columns("users")}


def test_unknown_operator_email_needs_the_seed_password(pg, monkeypatch):
    _pre_alembic_world(pg)
    monkeypatch.setenv("RECONFLOW_AUTO_MIGRATE", "1")
    monkeypatch.setenv("RECONFLOW_OPERATOR_EMAIL", "typo@x.test")
    with pytest.raises(RuntimeError, match="RECONFLOW_SEED_OPERATOR_PASSWORD is not set"):
        _fresh().ensure_schema()
    assert "alembic_version" not in inspect(create_engine(pg)).get_table_names()


def test_a_new_operator_account_is_created_by_the_startup_seed(pg, monkeypatch):
    _pre_alembic_world(pg)
    monkeypatch.setenv("RECONFLOW_AUTO_MIGRATE", "1")
    monkeypatch.setenv("RECONFLOW_OPERATOR_EMAIL", "official@x.test")
    monkeypatch.setenv("RECONFLOW_SEED_OPERATOR_PASSWORD", "a-long-seed-passphrase-1")
    _fresh()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        r = client.post("/auth/login", json={"email": "official@x.test", "password": "a-long-seed-passphrase-1"})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["role"] == "PLATFORM_OPERATOR" and r.json()["user"]["organization_id"] is None
    # the three existing accounts were not promoted, all belong to the organization
    assert _q(pg, "select count(*) from users where role='OWNER' and organization_id='o1'") == [(2,)]


def test_a_drifted_database_is_refused_untouched(pg, monkeypatch):
    _pre_alembic_world(pg, extra_column=True)
    monkeypatch.setenv("RECONFLOW_AUTO_MIGRATE", "1")
    monkeypatch.setenv("RECONFLOW_OPERATOR_EMAIL", "boss@x.test")
    with pytest.raises(RuntimeError, match="legacy_note"):
        _fresh().ensure_schema()
    assert "alembic_version" not in inspect(create_engine(pg)).get_table_names()
    assert _q(pg, "select count(*) from information_schema.schemata where schema_name like 'rf_baseline_%'") == [(0,)]


def test_without_the_switch_an_old_database_is_never_touched(pg, monkeypatch):
    _pre_alembic_world(pg)
    with pytest.raises(RuntimeError, match="RECONFLOW_AUTO_MIGRATE=1"):
        _fresh().ensure_schema()
    assert "alembic_version" not in inspect(create_engine(pg)).get_table_names()


def test_two_instances_starting_at_once_do_not_collide(pg, monkeypatch):
    env = dict(os.environ, RECONFLOW_AUTO_MIGRATE="1")
    code = "from bootstrap.schema import ensure_schema; ensure_schema(); print('ok')"
    procs = [subprocess.Popen([sys.executable, "-c", code], cwd=SRC, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(3)]
    results = [p.communicate(timeout=120) + (p.returncode,) for p in procs]
    assert all(rc == 0 and "ok" in out for out, err, rc in results), [(rc, err[-300:]) for out, err, rc in results]
    assert _q(pg, "select count(*) from alembic_version") == [(1,)]
