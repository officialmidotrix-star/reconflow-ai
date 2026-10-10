# ReconFlow — DECISIONS (updated 2026-10-09)

Read this first, then `docs/AUDIT_2026-10-07.md` (evidence and detail) and, for anything touching
production, `docs/BATCH1_RUNBOOK.md`.
Status legend: **AGREED** (user decided) · **PROPOSED** (mine, awaiting approval) · **OPEN** (needs input).

## 1. Repos and the code state this was written against
- Backend `officialmidotrix-star/reconflow-ai` — audit baseline `1bd9d7a`; Batch 1 is on branch
  `batch1-security-tenancy` (FastAPI, 18 modules, 306 tests pass + 10 PostgreSQL-only tests; Render + Render Postgres).
- Frontend `contactmidotrix-dot/reconflow-frontend` @ `625bbad` — React/Vite, still localhost + ngrok.
- Marketing `contactmidotrix-dot/v0-midotrix` @ `42dc277` — Next.js, EN/FR/AR. **Do not touch yet.**
- Always re-clone and read the real code before claiming anything; repo docs (`PROJECT_STATUS.md`) are stale.

## 2. Working process (AGREED, non-negotiable)
1. Audit first; report findings/design; wait for explicit approval before implementing non-trivial changes.
2. No fabricated data or numbers; say plainly what is unverified.
3. Frontend "done" = production build + strict type-check + Playwright visual check (viewports; light/dark and
   languages only where the app supports them) — never "should work".
4. Verify by running code and querying real databases, not by reasoning alone.
5. Real, clean git commit; deliver as a simple zip to extract-and-push in 2–3 commands (no diff/patch delivery —
   copy-paste corrupts on the user's Windows / Git Bash setup).
6. Never claim completion without end-to-end verification. Each batch is verified before the next starts.

## 3. Product decisions already in force (AGREED)
- Real categories produced today: Missing Settlement, Unexpected Settlement, Cancelled After Preparation,
  Settlement Amount Mismatch, Incorrect Commission (the last two need a commission contract — see §5).
- Multi-tenant architecture (one backend, one Postgres, tenant-scoped by organization/branch) is right.
  **The code is currently single-tenant** (one organization only) — implementation is Batch 1.
- External dispute PDF: platform-facing, factual third-person claim-letter voice; locked non-editable data table;
  AI chat edits surrounding narrative/tone only, with a backend system prompt hard-blocking any change to order
  references/dates/amounts. Unexpected Settlement is never in the external letter.
- Branch-Platform: a branch links to multiple delivery platforms (set up once at onboarding); every analysis scopes
  to a branch+platform pair.
- Login: one screen for all accounts; one accessible branch → straight in; several → a polished named picker;
  nobody ever types or sees a raw branch_id.
- Currency derives from a country field entered once at org/branch creation, not from file columns.
- Remove restaurant name/email/WhatsApp from the internal upload page (data belongs on Organization/Branch).
- Out of scope this round: in-app support button; direct POS/platform API integrations (manual upload stays).

## 4. Decisions made in this round (AGREED)
1. **Order:** security + tenancy → correctness of the numbers → performance → UX. Sequencing only; everything ships.
2. **Batch 1:** close `POST /users`; require a token and enforce org/branch access on every
   analysis/results/report route; stop the browser→public-n8n path bypassing per-user access; add Alembic.
3. **Brand:** app follows the Midotrix identity (purple/mint), adapted for data-heavy screens (strong contrast,
   readable tables). The external dispute PDF stays a neutral print-friendly white business document with a
   restrained accent — not dark-themed.
4. **Cancelled After Preparation:** off by default in claims; explicit per-case confirmation of who cancelled;
   tighten status matching beyond the loose substring.
5. **Roles:** OWNER sees all branches of their *own* organization automatically (including future ones). Other
   roles see only explicitly granted branches. Never cross-organization. A separate **platform-operator** role
   (the user) has cross-organization access for onboarding clients and running analyses on their behalf; it must be
   impossible for a client Owner to hold it. Users need an organization link; lift the single-org restriction.
6. **Arabic/RTL:** out of this round, but every new screen is RTL-ready (logical CSS properties, no hard-coded
   left/right). Server-side PDF must be able to handle Arabic shaping/RTL.
7. **Headline number (do right after Batch 1):** "leakage" = underpayments only, net of commission. Overpayments
   are not losses. Unexpected Settlement is excluded and shown separately as "to verify". Surface orders skipped for
   lack of a contract in the UI instead of skipping silently.
8. **Performance:** fix, then re-measure on **Postgres** (not only SQLite) with realistic non-exact-match inputs;
   plus a 100k-order test run the safe way (audit §5-D).
9. **Uploaded files:** a storage recommendation was requested (audit §5-B: S3-compatible object storage).
10. **Documents:** this audit and this file are to live in the backend repo.
11. **Marketing site:** list of required copy changes is in audit §5-E; do not edit until told. The only change
    wanted meanwhile is relabeling Sample Results as illustrative — to be requested separately.
12. **Batch 1 design (audit §7): approved as designed** (2026-10-09). Production is not touched until the user has
    run `bootstrap.verify_migration` on a restored dump and confirmed.
13. **Uploaded files → S3-compatible object storage** (decided; implementation is Batch 3, behind the existing
    `FileStorage` interface).
14. **Claim letter: English first.** Arabic is a later template change, after Batch 1, once the user confirms with
    a real platform contact what they accept. PDF library choice (WeasyPrint) holds either way.

## 5. Facts established (verified unless marked)
- Amount Mismatch / Incorrect Commission only fire when exactly one commission contract applies to the branch
  on the order date. None or ambiguous (e.g. a branch on two platforms) → every matched order is skipped silently;
  `skipped_no_contract_count` exists but is not shown in results.
- Current headline sums all categories and uses `abs()` of variances (overpayments counted as loss); Missing
  Settlement uses gross POS amount, not net of commission.
- The POS file has no channel column → dine-in/other-platform orders show as Missing Settlement.
- Authorization gaps and measured performance: audit §2–§4.
- Render plan, prod disk, whether `POST /users` is open on live, and the n8n workflow are **unverified**.
- Found while building Batch 1: (a) `get_encryption_key()` silently generates a throwaway key when
  `RECONFLOW_ENCRYPTION_KEY` is unset, making stored files unreadable after any restart; (b) unpinned
  `sqlalchemy>=2.0` + `psycopg2-binary` + a bare `postgresql://` URL breaks on SQLAlchemy 2.1 (default driver
  became psycopg v3) — fixed by `normalize_database_url` in `bootstrap/config.py`.
- How access control works now: every router is included with `access.enforce_policy`; every
  (method, route template) needs a row in `bootstrap/access.py::POLICIES` (deny by default; a test fails on a missing
  or stale row). Out-of-scope and nonexistent resources both answer the same generic 404. Roles: OWNER = all
  branches of own org (computed per request), other roles = explicit grants within own org, AUDITOR read-only,
  PLATFORM_OPERATOR = everything and never creatable over HTTP (DB CHECK `ck_users_operator_org`).
- Verified on PostgreSQL 16 (not SQLite only): fresh `upgrade head` + `alembic check` clean; a production-like
  database built by the ORIGINAL code's own API, dumped, restored and migrated with every invariant asserted
  (nothing lost, untouched tables byte-identical, one operator, audit attributed, downgrade/upgrade round trip,
  app boots); the four original exploits return 401/404 over real HTTP; existing accounts (incl. the n8n service
  account) keep working; a second client's Owner gets 404 on the first client's resources.

## 6. Plan (batches)
| Batch | Content | Status |
|---|---|---|
| 1 | Security + tenancy + Alembic | **Implemented and verified locally (incl. PostgreSQL 16); NOT deployed.** Needs the user's rehearsal on a real dump, then the cutover in `docs/BATCH1_RUNBOOK.md`. n8n token pass-through is **not built** — waiting for the workflow JSON (until then n8n's service account only reaches the first organization). |
| 2 | Numbers: headline definition, direction-aware variances, net of commission, "to verify", skipped-contract surfacing, tighter cancellation matching, POS source label design | not started — design needs approval first |
| 3 | Performance (N+1, fallback matching, blocking routes) + Postgres re-measure + 100k test; storage swap to object storage | not started |
| 4 | UX: branch_platforms schema, login→picker (`/auth/me/branches` already exists), upload cleanup, previous-analyses page, Midotrix-styled RTL-ready screens | not started |
| 5 | Dispute letter: server-side PDF (WeasyPrint), version+hash, claim reference, AI narrative editor with post-check; English first | not started |
| 6 | Deployment: n8n off Windows/ngrok, stable domain, CORS, SPA rewrite | not started |
Later/flagged: AI column mapping (human confirmation required), bank reconciliation, load-test harness. Tax/loyalty:
different problem class, needs an accountant — do not extend.

## 7. Open items
- **OPEN (user):** paste the n8n workflow JSON (credentials redacted) — unblocks the n8n token pass-through (needed
  before running analyses for a second client through n8n) and the node-level change list.
- **OPEN (user):** run `python -m bootstrap.verify_migration` on a restored copy of the real dump and report the
  result; then say go for the cutover. Pick which account becomes the operator (own admin login, not n8n's).
- **OPEN (user, read-only, 5 min):** the live Render checks in runbook §A (plans, DB expiry, disk, encryption key,
  whether `POST /users` still answers 422).
- **OPEN (user):** confirm with a real platform contact which language(s) a claim letter must be in.
- **PROPOSED (Batch 2):** when a contract rate is unavailable, do not guess net-of-commission — show the amount as
  unquantified "needs contract".
- **Housekeeping:** `reconflow_results_endpoints.diff` is a stray committed file in the backend repo; `docs/openapi.json`
  was regenerated in Batch 1.
