#!/usr/bin/env python3
"""
Load probe for the ReconFlow backend - synthetic data only, never real client data.

  python tools/load_probe.py --src apps/backend/src --db postgresql://u:p@localhost/scratch \
         --orders 15000 --scenario mixed [--mode stages|concurrency]

SAFETY: it refuses any database that is not on this machine, and it WIPES every
table in that database before it starts. Point it only at a throwaway database.

  --mode stages       runs the whole pipeline in-process and prints, per stage,
                      the time and the number of SQL statements.
  --mode concurrency  starts the real server (uvicorn) and runs the pipeline over
                      HTTP while 4 pollers hit /health and a small analysis; prints
                      how long a request has to wait while a heavy stage runs.

Scenarios (platform file vs POS file):
  clean     platform references equal POS references (everything matches in the exact pass)
  prefixed  every platform reference carries a prefix, so the exact pass finds nothing and
            the amount-and-date fallback has to do all the work (the worst case)
  mixed     85% exact, 15% prefixed; prices repeat (as restaurant prices do); 3% missing,
            2% amount mismatches (half under, half over), 1% cancelled, 1% refunded,
            1% unexpected settlements
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import random
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

PRICES = [18, 22, 25, 30, 35, 40, 45, 55, 60, 75, 90, 120]


def make_files(n: int, scenario: str, seed: int = 7) -> tuple[bytes, bytes]:
    rnd = random.Random(seed)
    base = datetime(2026, 3, 1, 9, 0, 0)
    pos, plat = [], []
    for i in range(n):
        t = base + timedelta(minutes=rnd.randint(0, 28 * 24 * 60))
        amount = rnd.choice(PRICES) + rnd.choice([0, 0, 0, 0.5])
        ref = f"{1_000_000 + i}"
        r = rnd.random()
        status = "Cancelled by customer" if r < 0.01 else ("Refunded" if r < 0.02 else "")
        pos.append((ref, t.strftime("%Y-%m-%d %H:%M:%S"), f"{amount:.2f}", status))
        if status or rnd.random() < 0.03:
            continue  # no settlement
        prefixed = scenario == "prefixed" or (scenario == "mixed" and rnd.random() < 0.15)
        gross = amount
        if rnd.random() < 0.02:
            gross += rnd.choice([-3, 3])
        plat.append((f"TLB-{ref}" if prefixed else ref, t.strftime("%Y-%m-%d %H:%M:%S"), f"{gross:.2f}", f"{gross * 0.15:.2f}"))
    for j in range(max(1, n // 100)):
        t = base + timedelta(minutes=rnd.randint(0, 28 * 24 * 60))
        plat.append((f"X-{j}", t.strftime("%Y-%m-%d %H:%M:%S"), "41.00", "6.15"))

    def csvb(header, rows):
        b = io.StringIO()
        w = csv.writer(b)
        w.writerow(header)
        w.writerows(rows)
        return b.getvalue().encode()

    return (csvb(["order_id", "order_time", "amount", "order_status"], pos),
            csvb(["order_id", "settlement_date", "gross_amount", "commission_amount"], plat))


def refuse_non_local(db_url: str) -> None:
    host = urlparse(db_url.replace("postgresql+psycopg2", "postgresql")).hostname
    if host not in ("localhost", "127.0.0.1", "::1", None):
        sys.exit(f"REFUSING: {host!r} is not this machine. The probe wipes the database it is given.")


def wipe(db_url: str) -> None:
    from sqlalchemy import create_engine, text

    from bootstrap.config import normalize_database_url  # noqa: F401  (imported after sys.path is set)
    e = create_engine(normalize_database_url(db_url), isolation_level="AUTOCOMMIT")
    with e.connect() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    e.dispose()


class Pipeline:
    """Same calls in both modes; `c` is a TestClient or an httpx.Client."""

    def __init__(self, c, op_email: str, op_pw: str) -> None:
        self.c = c
        tok = c.post("/auth/login", json={"email": op_email, "password": op_pw}).json()["token"]
        self.h = {"Authorization": f"Bearer {tok}"}
        org = c.post("/organizations", json={"legal_name": "TEST load org", "default_currency": "SAR"}, headers=self.h).json()["id"]
        self.branch = c.post("/branches", json={"organization_id": org, "name": "TEST branch", "timezone": "Asia/Riyadh"}, headers=self.h).json()["id"]
        pid = c.post("/platforms", json={"name": "Talabat"}, headers=self.h).json()["id"]
        c.post("/contracts", json={"branch_id": self.branch, "platform_id": pid, "commission_pct": "0.15",
                                   "valid_from": "2026-01-01", "valid_to": None}, headers=self.h)

    def new_analysis(self) -> str:
        r = self.c.post("/analyses", json={"branch_id": self.branch, "period_start": "2026-03-01", "period_end": "2026-03-31"}, headers=self.h)
        return r.json()["id"]

    def run(self, aid: str, pos: bytes, plat: bytes, timed) -> dict:
        c, h = self.c, self.h
        ids = {}
        for st, name, content in (("POS_EXPORT", "pos.csv", pos), ("PLATFORM_SETTLEMENT", "s.csv", plat)):
            r = timed(f"upload {st[:3]}", lambda st=st, name=name, content=content: c.post(
                f"/analyses/{aid}/files", data={"source_type": st}, files={"file": (name, content, "text/csv")}, headers=h))
            ids[st] = r.json()["id"]
        for st, fid in ids.items():
            r = timed(f"validate {st[:3]}", lambda fid=fid: c.post(f"/uploaded-files/{fid}/validation", headers=h))
            assert r.json()["status"] == "PASSED", r.text[:300]
        c.post(f"/analyses/{aid}/mark-processing", headers=h)
        for st, fid in ids.items():
            timed(f"normalize {st[:3]}", lambda fid=fid: c.post(f"/uploaded-files/{fid}/normalization", headers=h))
        out = {}
        for step in ("matching", "comparison", "discrepancies"):
            r = timed(step, lambda step=step: c.post(f"/analyses/{aid}/{step}", headers=h))
            assert r.status_code == 201, (step, r.text[:300])
            out[step] = r.json()
        c.post(f"/analyses/{aid}/mark-completed", headers=h)
        timed("GET summary", lambda: c.get(f"/analyses/{aid}/summary", headers=h))
        timed("GET discrepancies", lambda: c.get(f"/analyses/{aid}/discrepancies", headers=h))
        return out


def mode_stages(args) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import event

    import main
    from bootstrap.database import engine

    counter = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(*a, **k):
        counter["n"] += 1

    pos, plat = make_files(args.orders, args.scenario)
    print(f"orders={args.orders} scenario={args.scenario} pos={len(pos)//1024}KB platform={len(plat)//1024}KB")
    rows = []

    def timed(name, fn):
        counter["n"] = 0
        t0 = time.perf_counter()
        r = fn()
        rows.append((name, time.perf_counter() - t0, counter["n"]))
        return r

    with TestClient(main.app) as c:
        p = Pipeline(c, args.op_email, args.op_pw)
        out = p.run(p.new_analysis(), pos, plat, timed)
    total = 0.0
    for name, dt, n in rows:
        total += dt
        print(f"  {name:<20}{dt:8.2f}s   sql={n:>8}")
    print(f"  {'TOTAL':<20}{total:8.2f}s")
    import resource

    print(f"  peak memory of the whole process: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB")
    m, d = out["matching"], out["discrepancies"]
    print(f"  result: matched={m['matched_count']} unmatched_pos={m['unmatched_pos_count']} "
          f"unmatched_platform={m['unmatched_platform_count']} discrepancies={d['total_count']} excluded_refund_void={d.get('excluded_refund_void_count')}")


def mode_concurrency(args) -> None:
    import httpx

    port = 8790
    env = dict(os.environ, RECONFLOW_DATABASE_URL=args.db, RECONFLOW_STORAGE_DIR="/tmp/load_probe_files",
               RECONFLOW_ENCRYPTION_KEY="rXq2m6mO4m4g4q3Qz0m8Kx3l7Zr8mC1b9mN0oP5qTjE=",
               RECONFLOW_SEED_OPERATOR_EMAIL=args.op_email, RECONFLOW_SEED_OPERATOR_PASSWORD=args.op_pw)
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
                           cwd=args.src, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=600)
        for _ in range(60):
            try:
                c.get("/health")
                break
            except Exception:
                time.sleep(0.5)
        p = Pipeline(c, args.op_email, args.op_pw)
        small = p.new_analysis()  # something small for the pollers to read
        samples: list[tuple[float, float, str]] = []
        stop = threading.Event()

        def poller():
            cc = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=600)
            while not stop.is_set():
                for path in ("/health", f"/analyses/{small}"):
                    t0 = time.perf_counter()
                    cc.get(path, headers=p.h)
                    samples.append((t0, time.perf_counter() - t0, path))
                time.sleep(0.05)

        threads = [threading.Thread(target=poller, daemon=True) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(2)
        idle = [s[1] for s in samples]
        pos, plat = make_files(args.orders, args.scenario)
        spans: list[tuple[str, float, float]] = []

        def timed(name, fn):
            t0 = time.perf_counter()
            r = fn()
            spans.append((name, t0, time.perf_counter()))
            return r

        p.run(p.new_analysis(), pos, plat, timed)
        stop.set()
        for t in threads:
            t.join(timeout=5)

        def pct(xs, q):
            xs = sorted(xs)
            return xs[min(len(xs) - 1, int(q * len(xs)))] * 1000 if xs else float("nan")

        print(f"orders={args.orders} scenario={args.scenario}; idle requests: n={len(idle)} p50={pct(idle,.5):.0f}ms p95={pct(idle,.95):.0f}ms")
        print(f"{'stage':<20}{'took':>8}   waiting requests during it: n / p50 / p95 / MAX (ms)")
        for name, a, b in spans:
            if b - a < 0.5:
                continue
            during = [s[1] for s in samples if a <= s[0] <= b]
            print(f"  {name:<18}{b-a:7.1f}s   {len(during):>5} / {pct(during,.5):6.0f} / {pct(during,.95):6.0f} / {max(during, default=0)*1000:7.0f}")
    finally:
        srv.terminate()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="path to apps/backend/src of the code to measure")
    ap.add_argument("--db", required=True, help="a THROWAWAY local PostgreSQL (or sqlite) URL; it is wiped")
    ap.add_argument("--orders", type=int, default=5000)
    ap.add_argument("--scenario", choices=["clean", "prefixed", "mixed"], default="mixed")
    ap.add_argument("--mode", choices=["stages", "concurrency"], default="stages")
    ap.add_argument("--op-email", default="operator@probe.test")
    ap.add_argument("--op-pw", default="probe-operator-passphrase-1")
    args = ap.parse_args()
    args.src = str(Path(args.src).resolve())

    refuse_non_local(args.db)
    sys.path.insert(0, args.src)
    os.chdir(args.src)
    os.environ.update(RECONFLOW_DATABASE_URL=args.db, RECONFLOW_STORAGE_DIR="/tmp/load_probe_files",
                      RECONFLOW_ENCRYPTION_KEY="rXq2m6mO4m4g4q3Qz0m8Kx3l7Zr8mC1b9mN0oP5qTjE=",
                      RECONFLOW_SEED_OPERATOR_EMAIL=args.op_email, RECONFLOW_SEED_OPERATOR_PASSWORD=args.op_pw,
                      RECONFLOW_AUTO_MIGRATE="1")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    if args.db.startswith("postgres"):
        wipe(args.db)
    (mode_stages if args.mode == "stages" else mode_concurrency)(args)


if __name__ == "__main__":
    main()
