"""
Is a database's schema exactly the pre-tenancy baseline (migration 0001)?

Used before stamping an existing, never-migrated database as 0001: stamping
a database whose real schema differs would be a lie that later migrations
would trip over. The reference schema is built from migration 0001 itself in
a scratch SCHEMA of the same database (it needs only the CREATE privilege on
the database, which Render's default user has - no CREATE DATABASE) and is
dropped again afterwards.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

SRC_DIR = Path(__file__).resolve().parents[1]


def schema_shape(engine: Engine, schema: str | None = None) -> dict:
    insp = inspect(engine)
    shape = {}
    for t in sorted(insp.get_table_names(schema=schema)):
        if t == "alembic_version":
            continue
        cols = {
            c["name"]: (type(c["type"]).__name__, getattr(c["type"], "length", None), c["nullable"],
                        tuple(getattr(c["type"], "enums", None) or ()))
            for c in insp.get_columns(t, schema=schema)
        }
        idx = {i["name"]: tuple(i["column_names"]) for i in insp.get_indexes(t, schema=schema)}
        fks = sorted(
            (tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
            for f in insp.get_foreign_keys(t, schema=schema)
        )
        shape[t] = {"cols": cols, "idx": idx, "fks": fks}
    return shape


def diff_shapes(real: dict, ref: dict) -> list[str]:
    out = []
    for t in sorted(set(ref) - set(real)):
        out.append(f"table missing in your database: {t}")
    for t in sorted(set(real) - set(ref)):
        out.append(f"extra table in your database: {t}")
    for t in sorted(set(real) & set(ref)):
        for c in sorted(set(ref[t]["cols"]) - set(real[t]["cols"])):
            out.append(f"{t}.{c}: column missing in your database")
        for c in sorted(set(real[t]["cols"]) - set(ref[t]["cols"])):
            out.append(f"{t}.{c}: extra column in your database")
        for c in sorted(set(real[t]["cols"]) & set(ref[t]["cols"])):
            if real[t]["cols"][c] != ref[t]["cols"][c]:
                out.append(f"{t}.{c}: differs - yours {real[t]['cols'][c]} vs baseline {ref[t]['cols'][c]}")
        for i in sorted(set(ref[t]["idx"]) - set(real[t]["idx"])):
            out.append(f"{t}: index {i} missing in your database")
        if real[t]["fks"] != ref[t]["fks"]:
            out.append(f"{t}: foreign keys differ - yours {real[t]['fks']} vs baseline {ref[t]['fks']}")
    return out


def baseline_drift(engine: Engine, url: str) -> list[str]:
    """Differences between the database's public schema and migration 0001 ([] = identical)."""
    scratch = f"rf_baseline_{uuid.uuid4().hex[:8]}"
    with engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{scratch}"'))
    try:
        sep = "&" if "?" in url else "?"
        scratch_url = f"{url}{sep}options=-csearch_path%3D{scratch}"
        import os

        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0001"],
            cwd=SRC_DIR, env=dict(os.environ, RECONFLOW_DATABASE_URL=scratch_url),
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"could not build the baseline reference: {proc.stderr.strip()[-600:]}")
        return diff_shapes(schema_shape(engine, "public"), schema_shape(engine, scratch))
    finally:
        with engine.begin() as c:
            c.execute(text(f'DROP SCHEMA IF EXISTS "{scratch}" CASCADE'))
