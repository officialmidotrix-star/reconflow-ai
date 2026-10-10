"""
Rehearse the tenancy migration on a RESTORED COPY of the production database.

    # 1. take a dump of production, restore it into a throwaway LOCAL database
    # 2. from apps/backend/src:
    python -m bootstrap.verify_migration \\
        --url postgresql://user:pass@localhost:5432/restored_copy \\
        --operator-email you@example.com \\
        --confirm-copy

What it does, in order (and stops at the first failure):
  0. REFUSES to run unless the host is local (localhost / 127.0.0.1 / ::1 /
     unix socket) and you pass --confirm-copy. It cannot be pointed at a
     remote or hosted database.
  1. Snapshots the database: row counts, content hashes, users.
  2. Schema-drift check: builds a scratch database from migration 0001 on
     the same server and compares tables / columns / nullability / indexes /
     foreign keys / enum labels with your copy. If your real schema differs
     from the baseline, stamping 0001 would be a lie - it stops and tells you
     exactly what differs.
  3. Stamps 0001 if the copy has never been stamped, then runs
     `alembic -x operator_email=... upgrade head`.
  4. Asserts: nothing lost (row counts), nothing altered (hashes of every
     untouched table; users' ids/emails/password hashes), the named account is
     the only operator and has no organization, every other user belongs to the
     organization, audit entries are attributed to the right organization, the
     CHECK constraint exists AND rejects a bad insert.
  5. Proves reversibility: downgrade to 0001, assert the old shape and data are
     back, upgrade again, assert the same final state.
  6. Boots the real application against the migrated copy (schema check passes).
Exit code 0 only if everything passed. Nothing here touches any database other
than the copy you name (plus one scratch database it creates and drops).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

from bootstrap.config import normalize_database_url

SRC_DIR = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "", None}  # "" / None = unix socket
SKIP_HASH = {"alembic_version", "users", "audit_log_entries"}


class Failed(Exception):
    pass


def step(msg: str) -> None:
    print(f"\n== {msg}")


def ok(msg: str) -> None:
    print(f"   PASS  {msg}")


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise Failed(msg)
    ok(msg)


def alembic(url: str, *args: str) -> None:
    env = dict(os.environ, RECONFLOW_DATABASE_URL=url)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=SRC_DIR, env=env, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise Failed(f"alembic {' '.join(args)} failed:\n{proc.stderr.strip() or proc.stdout.strip()}")


# -- snapshots -------------------------------------------------------------


def snapshot(engine: Engine) -> dict:
    insp = inspect(engine)
    tables = sorted(t for t in insp.get_table_names() if t != "alembic_version")
    snap: dict = {"counts": {}, "hashes": {}}
    with engine.connect() as c:
        for t in tables:
            snap["counts"][t] = c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one()
            if t not in SKIP_HASH:
                snap["hashes"][t] = c.execute(
                    text(f'SELECT md5(COALESCE(string_agg(x::text, \'|\' ORDER BY x::text), \'\')) FROM "{t}" x')
                ).scalar_one()
        snap["users_core"] = c.execute(
            text("SELECT md5(COALESCE(string_agg(id || email || password_hash || is_active::text, '|' ORDER BY id), '')) FROM users")
        ).scalar_one()
        snap["audit_core"] = c.execute(
            text(
                "SELECT md5(COALESCE(string_agg(id || event || user_id || COALESCE(analysis_id,'') || details::text, '|' ORDER BY id), '')) "
                "FROM audit_log_entries"
            )
        ).scalar_one()
        snap["users"] = [
            tuple(r) for r in c.execute(text("SELECT email, CAST(role AS VARCHAR) FROM users ORDER BY email")).fetchall()
        ]
    return snap


def schema_shape(engine: Engine) -> dict:
    insp = inspect(engine)
    shape = {}
    for t in sorted(insp.get_table_names()):
        if t == "alembic_version":
            continue
        cols = {
            c["name"]: (type(c["type"]).__name__, getattr(c["type"], "length", None), c["nullable"],
                        tuple(getattr(c["type"], "enums", None) or ()))
            for c in insp.get_columns(t)
        }
        idx = {i["name"]: tuple(i["column_names"]) for i in insp.get_indexes(t)}
        fks = sorted(
            (tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
            for f in insp.get_foreign_keys(t)
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


# -- main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("RECONFLOW_DATABASE_URL"))
    ap.add_argument("--operator-email", required=True)
    ap.add_argument("--confirm-copy", action="store_true",
                    help="I confirm this is a restored COPY, not the production database.")
    args = ap.parse_args(argv)

    if not args.url:
        print("Pass --url (or set RECONFLOW_DATABASE_URL).", file=sys.stderr)
        return 2
    url = normalize_database_url(args.url)
    parsed = make_url(url)
    if not url.startswith("postgresql"):
        print("This rehearsal is for PostgreSQL copies.", file=sys.stderr)
        return 2
    if parsed.host not in LOCAL_HOSTS:
        print(f"REFUSING: host {parsed.host!r} is not local. Restore the dump into a local "
              "database and point --url at that.", file=sys.stderr)
        return 2
    if not args.confirm_copy:
        print("REFUSING: pass --confirm-copy to confirm this is a restored copy.", file=sys.stderr)
        return 2

    engine = create_engine(url)
    scratch_name = f"reconflow_verify_ref_{uuid.uuid4().hex[:8]}"
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        step("0. Target")
        print(f"   database: {parsed.database} on {parsed.host or 'unix socket'} (a local copy)")

        step("1. Snapshot before")
        before = snapshot(engine)
        for t, n in before["counts"].items():
            print(f"   {t:<28}{n}")
        emails = [e for e, _ in before["users"]]
        check(args.operator_email in emails,
              f"--operator-email {args.operator_email!r} is an existing account (accounts: {', '.join(emails)})")
        with engine.connect() as c:
            has_version = "alembic_version" in inspect(engine).get_table_names()
            current = c.execute(text("SELECT version_num FROM alembic_version")).scalar() if has_version else None
        print(f"   alembic_version: {current!r}")

        step("2. Schema-drift check against migration 0001")
        with admin.connect() as a:
            a.execute(text(f'CREATE DATABASE "{scratch_name}"'))
        scratch_url = parsed.set(database=scratch_name).render_as_string(hide_password=False)
        alembic(scratch_url, "upgrade", "0001")
        if current in (None, "0001"):
            diffs = diff_shapes(schema_shape(engine), schema_shape(create_engine(scratch_url)))
            check(not diffs, "your schema matches the baseline exactly" if not diffs else
                  "SCHEMA DRIFT - stamping 0001 would be wrong. Differences:\n      " + "\n      ".join(diffs))
        else:
            print(f"   (database is already at {current!r}; drift check against 0001 skipped)")

        step("3. Migrate")
        if current is None:
            alembic(url, "stamp", "0001")
            ok("stamped 0001")
        alembic(url, "-x", f"operator_email={args.operator_email}", "upgrade", "head")
        ok("upgraded to head")

        step("4. Assertions after upgrade")
        after = snapshot(engine)
        check(after["counts"].keys() == before["counts"].keys(), "no tables added or lost")
        check(all(after["counts"][t] == before["counts"][t] for t in before["counts"]),
              "every table has exactly the same number of rows")
        changed = [t for t in before["hashes"] if before["hashes"][t] != after["hashes"][t]]
        check(not changed, f"content of all {len(before['hashes'])} untouched tables is byte-identical")
        check(before["users_core"] == after["users_core"], "users: ids, emails, password hashes, active flags unchanged")
        check(before["audit_core"] == after["audit_core"], "audit log: existing entries' content unchanged")
        with engine.connect() as c:
            ops = c.execute(text("SELECT email, organization_id FROM users WHERE CAST(role AS VARCHAR)='PLATFORM_OPERATOR'")).fetchall()
            check([r[0] for r in ops] == [args.operator_email] and ops[0][1] is None,
                  f"exactly one operator ({args.operator_email}) and it has no organization")
            orphans = c.execute(text(
                "SELECT count(*) FROM users WHERE CAST(role AS VARCHAR)<>'PLATFORM_OPERATOR' AND organization_id IS NULL")).scalar_one()
            check(orphans == 0, "every client user belongs to an organization")
            orgs = {r[0] for r in c.execute(text("SELECT organization_id FROM users WHERE organization_id IS NOT NULL")).fetchall()}
            n_orgs = c.execute(text("SELECT count(*) FROM organizations")).scalar_one()
            check(len(orgs) <= n_orgs and orgs <= {r[0] for r in c.execute(text("SELECT id FROM organizations")).fetchall()},
                  "every user's organization exists")
            wrong = c.execute(text(
                "SELECT count(*) FROM audit_log_entries e JOIN analyses a ON a.id=e.analysis_id JOIN branches b ON b.id=a.branch_id "
                "WHERE e.organization_id IS DISTINCT FROM b.organization_id")).scalar_one()
            check(wrong == 0, "every audit entry tied to an analysis is attributed to that analysis's organization")
            unattributed = c.execute(text("SELECT count(*) FROM audit_log_entries WHERE organization_id IS NULL")).scalar_one()
            print(f"   info: {unattributed} audit entr{'y' if unattributed == 1 else 'ies'} not attributable to a client "
                  "organization (operator-only events) - visible to the operator only")
            check(c.execute(text("SELECT count(*) FROM pg_constraint WHERE conname='ck_users_operator_org'")).scalar_one() == 1,
                  "CHECK constraint ck_users_operator_org exists")
        with engine.connect() as c:
            trans = c.begin()
            try:
                c.execute(text(
                    "INSERT INTO users (id,email,password_hash,role,organization_id,is_active,created_at) "
                    "SELECT 'verify-bad', 'verify-bad@x', 'x', 'PLATFORM_OPERATOR', id, true, now() FROM organizations LIMIT 1"))
                raised = False
            except IntegrityError:
                raised = True
            finally:
                trans.rollback()
        check(raised, "the database REJECTS an operator that belongs to a client organization")

        step("5. Reversibility")
        alembic(url, "downgrade", "0001")
        down = snapshot(engine)
        check(down["counts"] == before["counts"] and down["hashes"] == before["hashes"],
              "after downgrade the old shape and all data are back")
        check(all(r != "PLATFORM_OPERATOR" for _, r in down["users"]),
              "after downgrade no user holds the operator role (old code cannot read it)")
        alembic(url, "-x", f"operator_email={args.operator_email}", "upgrade", "head")
        again = snapshot(engine)
        check(again["counts"] == after["counts"] and again["users"] == after["users"],
              "re-upgrade reaches the same final state")

        step("6. The real application boots against the migrated copy")
        env = dict(os.environ, RECONFLOW_DATABASE_URL=url, RECONFLOW_ENCRYPTION_KEY="rXq2m6mO4m4g4q3Qz0m8Kx3l7Zr8mC1b9mN0oP5qTjE=")
        proc = subprocess.run(
            [sys.executable, "-c",
             "from fastapi.testclient import TestClient; import main\n"
             "with TestClient(main.app) as c:\n"
             "    assert c.get('/health').json() == {'status': 'ok'}\n"
             "    assert c.post('/users', json={'email':'x@x.test','password':'long-enough-pass','role':'OWNER'}).status_code == 401\n"
             "print('app started; unauthenticated POST /users -> 401')"],
            cwd=SRC_DIR, env=env, capture_output=True, text=True,
        )
        check(proc.returncode == 0 and "app started" in proc.stdout, proc.stdout.strip().splitlines()[-1] if proc.returncode == 0
              else f"app failed to start:\n{proc.stderr[-800:]}")

        print("\nALL CHECKS PASSED. This rehearsal is on a copy; see docs/BATCH1_RUNBOOK.md for the production steps.")
        return 0
    except Failed as exc:
        print(f"\n   FAIL  {exc}", file=sys.stderr)
        print("\nSTOPPED. Do NOT run the migration on production.", file=sys.stderr)
        return 1
    finally:
        try:
            with admin.connect() as a:
                a.execute(text(f'DROP DATABASE IF EXISTS "{scratch_name}" WITH (FORCE)'))
        except Exception:  # noqa: BLE001
            pass
        engine.dispose()
        admin.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
