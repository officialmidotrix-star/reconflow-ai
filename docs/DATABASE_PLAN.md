# The free database: when to pay, and how to switch without losing data

**What Render says (its own documentation):**
- A free database **expires 30 days after it is created**. An expired one is closed until you upgrade it. You then have **14 days** to upgrade; after that Render **deletes it with all its data**.
- A free database has **no backups**, **1 GB** of space, and Render may restart it at any time.
- A paid database is the same database on a bigger plan: you upgrade it from the dashboard. Paid databases get **point-in-time recovery** (3 days on a free "Hobby" workspace, 7 days on higher workspace plans). Extra storage costs $0.30 per GB per month. The monthly price of each size is shown on the screen where you choose it.

**My honest rule: switch to paid BEFORE the first real client's data goes in — the day of their first real upload, not later.**
Reasons: the 30-day clock is a hard deadline; there are no backups on free; and a real client's financial data must never depend on a database that can vanish. While you only have test data, staying free is fine — when it expires, create a new empty one (the app rebuilds itself, see `docs/CUTOVER_ON_RENDER.md`).

**How to switch without losing data (clicks only):**
1. Render → your Postgres → **Info** → change the instance type to the smallest paid one → confirm. It is the same database: your data stays.
2. Open **Connect** on the same page. If the Internal Database URL is different from the one in `RECONFLOW_DATABASE_URL`, paste the new one there (Save, rebuild, and deploy). If it is the same, do nothing.
3. Check the database page now shows backups / point-in-time recovery.
4. Do this *before* day 30 (set a calendar reminder for day 25). If you miss it, you still have the 14-day grace period — but the database is closed during it, so the app is down.

**Do not** rely on creating a new free database each month once real data exists: that is how data is lost.
