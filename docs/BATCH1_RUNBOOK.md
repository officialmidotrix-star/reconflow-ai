# ADVANCED — rehearse the migration on a copy of the database (use this once the data is real)

> **For today (test data only) you do not need this file.** The simple path is `docs/CUTOVER_ON_RENDER.md`:
> the migration runs on Render by itself when the new version starts. Come back here when the database holds
> real client data: a rehearsal on a copy, a downloaded backup and a paused n8n are then worth the half hour.

(The scripts below need a GitHub Codespace or a PC with Python and PostgreSQL; the cutover script in Part 3
is the manual alternative to the deploy-time migration.)

Your admin login (the operator account): **official.midotrix@gmail.com**.
`<…>` = something you fill in. "Terminal" = the terminal inside the Codespace (Part 2A) or Git Bash (Part 2B).

**Nothing in this guide touches production until Part 3, step 2.** Parts 1 and 2 only *read* from production.

| Part | What | Time | Production touched? |
|---|---|---|---|
| 1 | Look at Render, check the hole is open | 10 min | no (read-only) |
| 2 | Rehearsal on a copy | 30 min | no |
| 3 | Cutover | 20 min | **yes** |
| 4 | Rotate two secrets | 20 min | yes (passwords only) |
| 5 | If something goes wrong | — | — |

## Which way to run the rehearsal — my recommendation: A (a GitHub Codespace)

| | What it is | Installs on your PC | Verdict |
|---|---|---|---|
| **A. Codespace** | A temporary Linux computer in your browser, opened on the repo. One command runs everything. | **none** | **Recommended.** I tested these exact scripts. GitHub gives personal accounts 120 core-hours/month free (≈60 hours on the smallest machine). Catch: repos owned by an *organization* get no free allowance — open github.com/officialmidotrix-star: if it says "Organization", use B. |
| B. Windows + Git Bash | Install Python and PostgreSQL on your PC, then type the steps yourself. | 2 programs | Works, more steps, **I have not run it on Windows**. |
| C. Temporary Render database | Render can clone a *paid* database by point-in-time recovery. | none | **Not recommended.** You would still need somewhere to run the check, and my safety guard only allows a local database, so a remote copy cannot be used with it. |

---

## Part 1 — Look before you touch (10 minutes, read-only)

1. **Render dashboard → your Postgres database → Info.** Write down: plan (Free or paid), PostgreSQL version, expiry date (free databases expire).
2. **Render dashboard → your web service → Environment.** Confirm `RECONFLOW_ENCRYPTION_KEY` exists. (If it is missing the app invents a new key on every restart and old uploaded files become unreadable — tell me if it is missing.) Note whether a *Disk* is attached.
3. **Is the hole open today?** Run in Git Bash, any folder (this creates nothing):
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<your-api>.onrender.com/users -H 'Content-Type: application/json' -d '{}'
   ```
   `422` = anyone can still create accounts (Part 3 closes it). `401` = already closed.
4. **Copy the database's External Database URL:** Render → your Postgres → **Connect** → **External** → copy. It contains the password: do not paste it in chat or in files. You will paste it into a hidden prompt.

## Part 2A — Rehearsal in a Codespace (recommended)

1. On GitHub open the backend repo (`officialmidotrix-star/reconflow-ai`). Click the branch dropdown, choose **`batch1-security-tenancy`**. Click the green **Code** button → **Codespaces** tab → **Create codespace on batch1-security-tenancy**. Wait until an editor opens in your browser (1–2 minutes).
2. In the editor: menu **Terminal → New Terminal**. It opens in the repo's top folder — leave it there.
3. Run (folder: the terminal's default, the repo top folder):
   ```bash
   bash scripts/rehearse.sh official.midotrix@gmail.com
   ```
   Paste the External Database URL when asked (nothing shows while you type) and press Enter. It takes 5–10 minutes: it copies the database (read-only), installs a temporary PostgreSQL inside the Codespace, and runs every check.
4. **Success** ends with a green **REHEARSAL PASSED**. **Failure** ends with a red **STOPPED** and `FAIL` lines. Either way production is untouched. If it failed, copy the `FAIL` lines to me.
5. Tell me the result. **Keep this Codespace open** (or reopen it from github.com/codespaces): Part 3 needs it.

## Part 2B — Rehearsal on Windows with Git Bash (only if you cannot use A)

Install once: **Python 3.12** from python.org (tick "Add python.exe to PATH") and **PostgreSQL** from postgresql.org/download/windows — use the **same major version as Render** (Part 1 step 1); remember the `postgres` password you set; keep "Command Line Tools". Then, in **Git Bash**, folder = the repo top folder (`…\reconflow-ai`):

1. `git fetch origin && git checkout batch1-security-tenancy`
2. `export PATH="$PATH:/c/Program Files/PostgreSQL/<version>/bin"` (replace `<version>`, e.g. `16`)
3. `python -m venv .venv-rehearsal && source .venv-rehearsal/Scripts/activate && pip install -r apps/backend/requirements.txt`
4. `psql -h localhost -U postgres -c "CREATE DATABASE rehearsal_copy"` (asks for the postgres password)
5. `pg_dump '<External Database URL>' --no-owner --no-privileges -f prod_dump.sql`
6. `psql -v ON_ERROR_STOP=1 -q 'postgresql://postgres:<password>@localhost:5432/rehearsal_copy' -f prod_dump.sql`
7. Folder `apps/backend/src`:
   `python -m bootstrap.verify_migration --url 'postgresql://postgres:<password>@localhost:5432/rehearsal_copy' --operator-email official.midotrix@gmail.com --confirm-copy`
8. Must end with `ALL CHECKS PASSED`. Then delete `prod_dump.sql` (it contains your data). For the cutover use the commands in Part 3B.

## Part 3 — Cutover (only after the rehearsal passed AND you told me to go)

1. **Pause n8n:** open the ReconFlow workflow in n8n and switch it to inactive.
2. **Migrate production.** In the same Codespace terminal (folder: repo top folder):
   ```bash
   bash scripts/cutover.sh official.midotrix@gmail.com
   ```
   Paste the **production** External Database URL (hidden), then type exactly `MIGRATE PRODUCTION`. The script refuses unless the rehearsal passed in this Codespace, for this email, for this same database, in the last 48 hours. It then makes a backup, migrates, and checks. Success ends with a green **DATABASE MIGRATED**.
3. **Keep the backup:** left panel → folder `backups` → right-click the `.dump` file → **Download**.
4. **Deploy the new code now** (the old code is still running until you do; do it within a few minutes of step 2): on GitHub → **Pull requests → New pull request** → base `main`, compare `batch1-security-tenancy` → **Create pull request → Merge**. Render redeploys automatically; watch Render → your web service → **Events** until it says live. (If auto-deploy is off: **Manual Deploy → Deploy latest commit**.) If the deploy fails with "Database schema is at revision…", step 2 did not finish — tell me.
5. **Smoke test** (Git Bash or the Codespace terminal, any folder). Replace the address:
   ```bash
   API=https://<your-api>.onrender.com
   curl -s -o /dev/null -w "create user, no login  -> %{http_code} (want 401)\n" -X POST $API/users -H 'Content-Type: application/json' -d '{}'
   curl -s -o /dev/null -w "read analysis, no login -> %{http_code} (want 401)\n" $API/analyses/anything
   read -rsp "Your admin password: " PW; echo
   curl -s -X POST $API/auth/login -H 'Content-Type: application/json' -d "{\"email\":\"official.midotrix@gmail.com\",\"password\":\"$PW\"}" | sed -E 's/"token":"[^"]+"/"token":"…"/'
   ```
   The last line must show `"role":"PLATFORM_OPERATOR"`.
6. **Switch n8n back on** and run one real analysis end to end through the frontend. n8n's own account is unchanged and keeps working (I tested this on a copy).
7. **Delete the Codespace** (it holds a copy of production data): github.com/codespaces → **⋯** next to it → **Delete**.

### Part 3B — Cutover from Windows (only if you did 2B)
Git Bash, folder `apps/backend/src`, venv active:
```bash
export RECONFLOW_DATABASE_URL='<production External Database URL>'
pg_dump "$RECONFLOW_DATABASE_URL" -Fc --no-owner -f ../../../before_batch1.dump     # backup first; keep this file
alembic stamp 0001
alembic -x operator_email=official.midotrix@gmail.com upgrade head
alembic current                                                                      # must say: 0002 (head)
```
Then continue with step 4 above.

---

## Part 4 — Rotate two secrets
See `docs/ROTATE_SECRETS.md`.

## Part 5 — If something goes wrong
- **Rehearsal failed:** nothing was changed; send me the `FAIL` lines.
- **Cutover failed before "DATABASE MIGRATED":** the script stops by itself. Production is either untouched or recoverable: send me the output, do not retry blindly.
- **After the new code is live and something is wrong:** tell me; do not delete anything. The backup you downloaded restores the old state (I will give you the exact restore command). While no second client exists, a clean step back is also possible: `alembic downgrade 0001` plus Render → **Rollbacks** to the previous deploy.
- **Locked out of the admin account:** in a terminal on the repo (folder `apps/backend/src`, with `RECONFLOW_DATABASE_URL` set): `python -m bootstrap.create_operator --email <email>`.

## Known limits after Batch 1
- **n8n:** its account is an Owner of the first client only. A second client's analyses through n8n need the token pass-through I will design once I have the workflow JSON.
- **Audit log:** events by the account that became the operator and not tied to an analysis are visible to the operator only.
- **Not yet covered:** brute-force protection on login; a minimum password length when *creating* users (the new change-password route enforces 12 characters).
