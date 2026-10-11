# Deploy on Render with a brand-new empty database — nothing to install

1. Render → **New → Postgres** → same region as your web service → **Create**. Wait until it says *Available*.
2. On that database's page: **Connect → Internal** → copy the **Internal Database URL**.
3. Web service → **Environment**. Change `RECONFLOW_DATABASE_URL` → paste the URL. Delete `RECONFLOW_SEED_OWNER_EMAIL` and `RECONFLOW_SEED_OWNER_PASSWORD` if they exist.
4. Add `RECONFLOW_AUTO_MIGRATE` = `1`
5. Add `RECONFLOW_OPERATOR_EMAIL` = `official.midotrix@gmail.com`
6. Add `RECONFLOW_SEED_OPERATOR_PASSWORD` = a new strong password (12+ characters; write it down). Check `RECONFLOW_ENCRYPTION_KEY` is in the list (never change it). For the **test deploy** also add `RECONFLOW_SEED_DEMO_DATA` = `1` (clearly labelled test data, see `docs/sample_files/README.md`); remove it before real clients. All settings: `docs/SETTINGS.md`.
7. Click the small arrow on the save button → **Save only**. (This way the old code never touches the new database.)
8. GitHub → **Pull requests → New** → base `main`, compare `batch3-cumulative` (your newest branch) → **Create → Merge**. Render deploys by itself.
9. Render → web service → **Logs**: wait for `Migration complete: database is at 0002` and `Your service is live`. Log in with `official.midotrix@gmail.com` and the password from step 6, then delete `RECONFLOW_SEED_OPERATOR_PASSWORD` (Save only).
If the logs say **"Refusing to migrate…"**: nothing was changed and the old version keeps running. Send me that one line.

## What the first start does (I ran this exact start on an empty PostgreSQL)
It builds all 30 tables, records revision 0002, creates your operator account, and answers `401` to anyone who is not logged in. Every later start does nothing to the database.

## Which safety steps you may skip (you only have test data)
- **Skip:** rehearsal on a copy, backup, pausing n8n, checking the database by hand.
- **Why it is safe:** the new version refuses to start if anything looks wrong, and Render keeps the old one running. A failed migration is rolled back by PostgreSQL.
- **Do not skip:** steps 3–7 exactly as written, and reading the logs in step 9.
- **Fastest fix for any problem:** create another empty database and repeat steps 1–3. You lose only test data.
- **Come back to the careful way** (`docs/BATCH1_RUNBOOK.md`) the day the database holds real client data.
