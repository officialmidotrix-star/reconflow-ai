# Batch 1 runbook — tenancy, access control, migrations

Nothing here has been run against production. Order is strict: **A → B → (your go) → C**.
Commands are written for Git Bash on Windows; `<angle brackets>` are values you fill in.

## A. Five minutes of looking at live Render (read-only)

1. **Postgres:** instance type, **expiry date** (free instances expire), and the major version (use the same one locally in B).
2. **Web service:** plan/instance type; whether a **persistent disk** is attached (without one, uploaded files and generated reports vanish on every redeploy).
3. **Environment variables:**
   - `RECONFLOW_ENCRYPTION_KEY` **must be set and never change.** The code (`bootstrap/config.py`) generates a throwaway key when it is missing, so every restart would make previously stored files unreadable.
   - `RECONFLOW_DATABASE_URL` — set. (A plain `postgresql://` URL is now pinned to the psycopg2 driver in code; before this change an unpinned dependency upgrade could have broken the connection.)
   - `RECONFLOW_ALLOWED_ORIGINS` — must contain your frontend's origin once it leaves localhost.
   - `RECONFLOW_SEED_OWNER_EMAIL/PASSWORD` — no longer used (logged as a warning). Remove them when convenient.
4. Is the hole open today? This probe **creates nothing**: it sends an empty body. The old code has no
   authentication on this route, so it validates the body and answers `422`; closed code answers `401`.
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<your-api>.onrender.com/users \
     -H 'Content-Type: application/json' -d '{}'
   ```
   `422` = anyone on the internet can still create accounts (the Batch 1 deploy closes it); `401` = already closed.

## B. Rehearsal on a restored copy (this is the step that gates production)

1. **Dump production** (read-only; use the *external* connection string from Render's Postgres page):
   ```bash
   pg_dump "<external-database-url>" --no-owner --format=plain -f prod_dump.sql
   ```
2. **Restore into a throwaway local Postgres** (same major version as production):
   ```bash
   docker run --name rf-copy -e POSTGRES_PASSWORD=rf -p 55432:5432 -d postgres:16
   docker exec -it rf-copy psql -U postgres -c "CREATE DATABASE restored;"
   docker exec -i rf-copy psql -U postgres -d restored < prod_dump.sql
   ```
3. **Run the rehearsal** from the repo, pointing it at the *copy*:
   ```bash
   cd apps/backend && pip install -r requirements.txt && cd src
   python -m bootstrap.verify_migration \
     --url postgresql://postgres:rf@localhost:55432/restored \
     --operator-email <the email you log in with as yourself> \
     --confirm-copy
   ```
   - It **refuses any non-local host** and refuses to run without `--confirm-copy`.
   - Promote **your own admin login**, not the n8n service account. The n8n account stays an ordinary OWNER of the existing organization (see "Known limits").
   - Expected last line: `ALL CHECKS PASSED.` It checks: schema drift against the baseline, no rows lost, every untouched table byte-identical, exactly one operator, every client user in an organization, audit attribution, the CHECK constraint rejects a bad insert, downgrade → upgrade round trip, and that the real app boots on the migrated copy.
   - **Any FAIL → stop and send me the output.** A *schema drift* failure means production's real schema differs from what the baseline migration assumes; stamping would be wrong.
   - The script is not re-runnable on the same copy (it leaves it migrated): drop and re-restore to run again.

## C. Production cutover (only after B passes and you say go)

1. **Backup** (keep the file): `pg_dump "<external-database-url>" -Fc -f before_batch1.dump`.
2. **Pick a quiet moment and pause n8n runs.** Between steps 3 and 4 the old code is still running against the new schema; the promoted account cannot log in on old code (it does not know the new role), other accounts are fine. Keep this window to minutes.
3. **Migrate** (from your machine, against production):
   ```bash
   cd apps/backend/src
   export RECONFLOW_DATABASE_URL='<external-database-url>'
   alembic stamp 0001
   alembic -x operator_email=<same email as in B> upgrade head
   alembic current            # expect: 0002 (head)
   ```
4. **Deploy the code** (push the branch / merge to the branch Render deploys). On start the app verifies the database is at head and **refuses to start otherwise**; Render then keeps the previous deploy serving.
5. **Smoke test** (replace host/credentials):
   ```bash
   H=https://<your-api>.onrender.com
   curl -s -o /dev/null -w "POST /users, no token      -> %{http_code} (want 401)\n" -X POST $H/users -H 'Content-Type: application/json' -d '{"email":"x@x.invalid","password":"long-enough-pass","role":"OWNER"}'
   curl -s -o /dev/null -w "GET /analyses/<id>, no token -> %{http_code} (want 401)\n" $H/analyses/<any-analysis-id>
   curl -s -o /dev/null -w "GET /reports/<id>/download   -> %{http_code} (want 401)\n" $H/reports/<any-report-id>/download
   TOKEN=$(curl -s -X POST $H/auth/login -H 'Content-Type: application/json' -d '{"email":"<you>","password":"<pw>"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
   curl -s $H/auth/me -H "Authorization: Bearer $TOKEN"            # role PLATFORM_OPERATOR, organization_id null
   curl -s $H/auth/me/branches -H "Authorization: Bearer $TOKEN"    # every branch
   ```
   Then run one real analysis end to end through n8n and the frontend.
6. **Future migrations** run automatically: set Render's *Pre-Deploy Command* to `cd apps/backend/src && alembic upgrade head`.

## D. Rollback
- **Before the new code is deployed (and no second client exists yet):** `alembic downgrade 0001` (operator becomes an ordinary OWNER) and keep running the old code. The rehearsal proves this round trip.
- **Otherwise:** restore `before_batch1.dump`. Downgrading after a second organization exists would leave users without an organization link, so use the backup.

## E. Day-to-day after Batch 1
- **New client:** sign in as the operator → `POST /organizations` → `POST /users` (`role: OWNER`, `organization_id`) → the client's Owner then creates branches and users in their own organization. An Owner sees every branch of their organization automatically, including future ones; other roles need `POST /users/{id}/branch-access`.
- **Lost operator access:** `cd apps/backend/src && python -m bootstrap.create_operator --email <email>` (hidden password prompt), or `--promote-existing`. There is deliberately no HTTP route that can create an operator.
- **Optional seeding:** set `RECONFLOW_SEED_OPERATOR_EMAIL` / `RECONFLOW_SEED_OPERATOR_PASSWORD` to create an operator on first start (never modifies an existing account).

## F. Known limits after Batch 1
- **n8n:** it still logs in with its own service account (an OWNER of the existing organization). That keeps working, but it can only see that one organization, so running analyses for a *second* client through n8n needs the planned token pass-through (browser sends the user's token → n8n forwards it). That design needs the n8n workflow JSON.
- **Audit log:** entries made by the account that became the operator, and not tied to an analysis (e.g. creating the first organization), are visible to the operator only.
- **Roles:** the audit log is readable by OWNER (own organization) and the operator; deployment endpoints are operator-only; AUDITOR is read-only.
- Not audited: login brute-force protection; very large multipart uploads are parsed before authentication (FastAPI reads the form first).
