# ReconFlow AI — Project Status

**Status:** All 16 backend modules implemented, tested, and composed into one running FastAPI application. 217/217 tests passing.

## Architecture

A modular monolith — one deployable FastAPI service, internally organized as 16 independent modules under `apps/backend/src/modules/`, each with its own models, service (business logic), exceptions, and router. Modules depend on each other's *real* tables read-only once built, and on Protocol-typed stubs (with in-memory test doubles) for modules not yet built — every stub was later replaced by a real implementation and proven compatible via a dedicated integration test before the stub was retired.

Cross-cutting infrastructure lives outside any single module: `db/` (shared SQLAlchemy `Base` and a `UTCDateTime` type that fixes SQLite's tendency to silently drop timezone info), `storage/` (encrypted file storage, shared by Data Import and Reporting), and `bootstrap/` (the application composition root — database setup, and every module's dependency wiring).

n8n orchestrates the reconciliation pipeline in production (calling each stage's endpoint in sequence, then Analysis Orchestration's status checkpoints); it was never simulated or generated in this codebase, by design — n8n workflow JSON was explicitly out of scope throughout.

## Key decisions

- **AI stays swappable and grounded.** AI Insights reads only aggregate figures (never raw transactions or PII), and every generated number is checked against the input after generation — an ungrounded figure is rejected outright, not just discouraged by the prompt.
- **`ReconciliationMatch` is soft-superseded, never deleted.** A late discovery: Matching Engine's rerun logic originally hard-deleted old matches, which would have orphaned Manual Review's permanent audit trail the moment a match was reviewed and then re-matched. Fixed by marking old rows superseded instead, with Financial Comparison and Discrepancy Detection filtering to active rows only.
- **Commission contracts are temporally versioned.** A past analysis always reconciles against the rate in effect at the time, even after the rate is later changed.
- **A branch with concurrent contracts across platforms is a known, accepted gap.** Nothing upstream (Data Import) captures which delivery platform a settlement file is from, so an ambiguous rate lookup returns "no contract configured" rather than guessing — the real fix is upstream, not in Reference & Contract Configuration.
- **Audit Logging has no dependencies of its own** — the one true leaf in the module graph — and `log()` never raises, even on a database failure, since none of the 14 calling modules were built to handle that.
- **Licensing is self-reported, not enforced, by design.** `GET /deployment/info` is the only unauthenticated endpoint in the app, deliberately, so support tooling can check version/license status without a login; it never returns the raw license key.
- **Passwords use salted PBKDF2-HMAC-SHA256** (stdlib only); sessions are opaque server-side tokens, not JWTs — only a token's hash is ever persisted, so revocation is an immediate database write.

## The 16 modules

| # | Module | Tests | Role |
|---|---|---|---|
| 1 | Identity & Access | 17 | Users, password auth, sessions, branch-access grants |
| 2 | Organization & Branch Management | 14 | Singleton org, branches, timezone/currency source of truth |
| 3 | Reference & Contract Configuration | 16 | Delivery platforms, temporally-versioned commission contracts |
| 4 | Data Import | 13 | Upload intake, encrypted storage, duplicate detection |
| 5 | Data Validation | 14 | Structural/value checks before normalization |
| 6 | Data Normalization | 12 | Canonical `Transaction` rows: UTC timestamps, quantized amounts |
| 7 | Matching Engine | 15 | Exact-reference + amount/date fallback matching |
| 8 | Financial Comparison | 12 | Expected vs. actual commission and settlement variance |
| 9 | Discrepancy Detection & Classification | 18 | Categorizes and severity-scores what Comparison/Matching found |
| 10 | Manual Review & Override | 16 | Human confirm/reject/dispute/manual-pairing, permanent audit trail |
| 11 | AI Insights | 9 | Grounded executive summaries from aggregate discrepancy data |
| 12 | Analysis Orchestration | 15 | The real `Analysis` entity, status state machine, versioning |
| 13 | Reporting & Export | 10 | CSV/XLSX report generation and download |
| 14 | Notification | 6 | Completion alerts via a swappable channel |
| 15 | Audit Logging | 12 | The real, persistent audit trail all 15 other modules write to |
| 16 | Deployment, Update & Licensing | 15 | Version/license self-reporting, manual-for-MVP by design |

Plus 3 application-level tests proving the full pipeline works end-to-end through real HTTP against the composed app.

## What's genuinely outside this backend's scope

- **No frontend.** The web application was designed in Phase 2 but never built — every endpoint exists, but nothing renders a UI against them yet.
- **No n8n workflows.** Orchestration logic is designed for n8n to call into; the workflows themselves were never generated, by design.
- **No production deployment automation.** `bootstrap/` wires one runnable app; containerization, CI/CD, and infra-as-code were never in scope.
- **Deliberately deferred, not missed:** PDF reports, cryptographic license-key verification, platform-level disambiguation in commission lookups — each flagged explicitly at the point it came up, not overlooked.
