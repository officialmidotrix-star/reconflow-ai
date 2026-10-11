# Speed and size limits — in simple words

**What was wrong (measured, not guessed).** Two things made the service slow, and one made it freeze:
1. The "comparison" step asked the database about 4 questions *per order*.
2. When the platform's order numbers did not match the POS's, the "matching" step compared every order with every other.
3. While one analysis ran a long step, **every other request in the whole service waited for it** — other users, and Render's own health check.

**What changed.** The database is asked once instead of thousands of times; matching uses an index; long steps now run beside each other instead of blocking everything. Results are identical (a randomized test compares the new matching with the old on 720 random cases; the counts above did not move).

**Measured on PostgreSQL 16, same data before and after** (a developer machine, database on the same machine — Render will be slower in absolute terms; trust the ratios):

| Test | Before | After |
|---|---|---|
| 5,000 orders, whole analysis | 13.0 s | **4.9 s** |
| 5,000 orders, "comparison" step | 9.4 s (19,016 queries) | **1.2 s (75 queries)** |
| 5,000 orders, platform numbers don't match (worst case), "matching" step | 18.7 s | **1.0 s** |
| Longest wait of another user's request during a heavy step | **15.1 s** | **0.3 s** |

**How it grows** (after the change, mixed realistic data):
| Orders in one analysis | 10,000 | 25,000 | 50,000 | 100,000 |
|---|---|---|---|---|
| Whole analysis | 9 s | 25 s | 45 s | 79 s |
| Memory used | 191 MB | 328 MB | 541 MB | 992 MB |

Time grows in a straight line. **Memory is the real limit:** about 9 KB per order on top of about 100 MB. From these measurements (not tested on Render itself): a **512 MB** instance handles roughly **35,000–40,000 orders per analysis**; **1 GB** about 90,000; **2 GB** about 200,000. One restaurant branch for one month is usually a few thousand orders, so this only matters for a whole chain in one file.

**Not changed (on purpose, for later):** the list of all problems is returned in one piece (no pages) — fine up to a few thousand rows; reading and checking the uploaded files is already linear.

**To repeat the measurement** (a developer, on a scratch database — it wipes it): `python tools/load_probe.py --src apps/backend/src --db postgresql://… --orders 50000 --scenario mixed` (add `--mode concurrency` for the waiting test). It refuses any database that is not on the same machine.
