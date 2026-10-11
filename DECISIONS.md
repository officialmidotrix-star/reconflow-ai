# ReconFlow — DECISIONS (updated 2026-10-10)

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

## 6. Plan (batches) — one zip per batch, built on its own branch, tests green before delivery
| Batch | Content | Status |
|---|---|---|
| 1 | Security, tenancy, Alembic | Delivered (zip 1). Applied by the user on branch `batch1-v2`; NOT deployed. |
| 1.1 | Migration runs on Render at deploy time (guarded); change/reset password; list users; `.gitignore` | Delivered (zip 1.1) |
| 2 | Numbers: headline = recoverable underpayments net of commission; overpayments are not losses; Unexpected → "to verify"; skipped-contract surfaced; stricter cancellation matching; per-case cancellation confirmation | Delivered (zip 2) — see `docs/NUMBERS.md` |
| 3 | Performance (N+1, fallback matching, blocking routes) + Postgres re-measure + 100k test; S3 storage class (switched off until the user chooses a provider — money); labelled test data + sample files | Delivered — cumulative zip (1.1 + 2 + 3), branch `batch3-cumulative`; see `docs/PERFORMANCE.md` |
| 4 | UX: branch_platforms schema, login→picker, upload cleanup, previous analyses, Midotrix look, RTL-ready | after 3 |
| 5 | Dispute letter (English first), server-side PDF, AI narrative editor with post-check | after 4 |
| 6 | Deployment: n8n off Windows/ngrok, stable domain, CORS | after 5 (needs the user's inputs) |

### Batch 2 decisions I made inside the agreed scope (reversible; change on request)
- Six treatments: RECOVERABLE, NEEDS_CONFIRMATION, NEEDS_CONTRACT, TO_VERIFY, NOT_CLAIMABLE, NEEDS_RERUN. Only RECOVERABLE is the headline.
- Net of commission = gross x (1 - contract rate), rounded half-up to cents. Commission overcharge is claimed as is (already net). A gross shortfall is claimed x (1 - rate); lower commission in the restaurant's favour is "to verify" and never subtracted (no double counting).
- Cancelled by PLATFORM or CUSTOMER = claimable; by RESTAURANT = not claimable. `CLAIMABLE_CANCELLERS` in `modules/discrepancies/models.py`.
- Refunded/void orders with no payout: excluded and counted, not claimed. A word meaning "cancelled" wins over a refund word.
- Old discrepancy rows are labelled, never guessed (migration 0003): they show NEEDS_RERUN until the analysis is run again.
- `total_potential_revenue_leakage` stays in the API as a deprecated alias of the new headline so a frontend deployed before the change shows the corrected number.
- Not in Batch 2 (by design): the POS channel problem (dine-in / other-platform orders showing as missing) needs the branch+platform link and a POS source label (Batch 4); the current PDF still lists every row (Batch 5).

### Batch 3 facts
- Found by measurement on PostgreSQL: the comparison step ran ~4 queries per order; the matching fallback was quadratic (18.7 s at 5,000 orders when references differ); and **every long step froze the whole service** (other requests waited the full 15 s, Render's health check included) because handlers were `async def` doing blocking work. All three fixed; results identical (randomized equivalence test, same counts).
- 100,000 orders: 79 s, ~1 GB peak memory; time is linear, memory ~9 KB/order is the real limit (512 MB instance ≈ 35-40k orders per analysis, estimated).
- Engine: `pool_pre_ping`, bounded pool (10+10), request thread limit 16 (env-tunable).
- S3 storage (`RECONFLOW_STORAGE_BACKEND=s3`) is built and tested against a mock S3, OFF by default. Choosing a provider/bucket costs money: user decides.
- Test data (`RECONFLOW_SEED_DEMO_DATA=1`): org "TEST DATA - Demo Restaurants", 3 branches, test commission rates Talabat 20% / Jahez 15% / HungerStation 30% — TEST VALUES ONLY, one platform per branch until Batch 4.
- Merchant IDs: optional, blank by default (Batch 4 adds the field; the Batch 5 letter shows a blank line when empty).

## 7. Working rules from the user (2026-10-10) — these override earlier "wait for approval" habits
1. Everything already agreed in this file proceeds without asking again. Stop ONLY for: production data, money, irreversible steps.
2. Cutover must need nothing installed on the user's PC: the migration runs on Render at deploy time (`RECONFLOW_AUTO_MIGRATE=1`).
3. One zip per batch, built on a branch, tests green before delivery; each with a plain-language runbook of at most 10 lines
   (exact Git Bash commands + the folder). Always `git add` explicit paths, never `git add -A`.
4. Needs from the user are collected in ONE list (section 8), not asked one at a time.
5. Short explanations in simple words.
6. The user only has test data today: the careful rehearsal (`docs/BATCH1_RUNBOOK.md`) is for when data is real.

## 8. Everything needed from the user (asked once; none of it blocks work already possible)
- n8n workflow JSON, credentials redacted → token pass-through for a second client; node-level change list.
- ANSWERED (2026-10-11): no client yet → TEST commission rates, clearly labelled; merchant IDs optional and blank by default; one cumulative zip per batch; one test deploy after Batch 3 on a fresh database, one final deploy after all batches.
- (Still needed, later, per real client) the real commission contract per branch × platform, and the platform merchant/store ID if the platform asks for it.
- Country of each organization (SA / AE / QA / KW …) → currency.
- Sender details for the letter: legal name, address, contact person, phone, email, VAT/licence number if shown.
- A few real files (redacted is fine): a POS export and each platform's settlement report; and the name of the POS column (if any) that says an order came from Talabat/Jahez/etc.
- Platforms to support first (names).
- Claim-letter language beyond English: later, after a real platform contact confirms what they accept.
- MONEY decisions (I stop and wait): object-storage provider/bucket/region; domain name; where n8n will be hosted (a server).
- Logo file (SVG) and confirmation of the purple/mint tokens.
