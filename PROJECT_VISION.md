# ReconFlow AI — Project Vision

## The problem

Restaurants selling through delivery platforms (Talabat, Jahez, HungerStation, and similar) routinely lose money to reconciliation gaps between what their POS system recorded and what a platform actually settles: missing settlements, incorrect commission deductions, and settlement amounts that don't match the order value. This revenue leakage is hard to catch by hand, especially across multiple platforms and branches, and existing accounting workflows weren't built to compare two independent data sources against each other automatically.

ReconFlow AI exists to catch that leakage automatically, explain it in plain language, and give restaurant staff a clear, evidence-backed way to act on it — without requiring them to be financial analysts.

## Target users

- **Owner** — wants the headline number: how much are we losing, and is it getting better or worse.
- **Finance Manager** — needs the detail behind that number, broken down by platform and branch.
- **Accountant** — does the actual reconciliation work: reviewing matches, confirming or disputing discrepancies, producing reports.
- **Ops Manager** — cares about the operational side: which branch, which platform, which orders.
- **Auditor** — needs a trustworthy, permanent record of what happened and who decided what, without necessarily being able to change anything.

The system is explicitly designed to *assist* these people, not replace their judgment — matching and comparison are deterministic and automatic, but every automated conclusion can be reviewed, confirmed, or overridden by a human, and that human decision is recorded permanently.

## Design commitments that follow from the vision

- **Deterministic math, AI narration only.** The AI never computes anything and never introduces a figure that wasn't already calculated — its only job is explaining numbers a human can already trust, in plain language.
- **Self-hosted, one deployment per customer.** Each restaurant's data stays on its own isolated deployment; nothing is shared or pooled across customers.
- **Nothing is silently discarded.** Matches, reviews, and audit entries are kept as permanent history rather than overwritten, because the Auditor persona depends on that trail existing.

## What's built vs. what's next

The 16-module backend described in `PROJECT_STATUS.md` delivers the complete reconciliation *engine*: upload, validate, normalize, match, compare, classify, review, narrate, orchestrate, report, notify, and the identity/organization/contract/audit/deployment foundations underneath all of it. That engine is real, tested, and runs as one composed application today.

Three things were always understood to come after the engine, not instead of it:

1. **Deployment.** Packaging the application for real installation — containerization, environment provisioning, and the operational tooling a self-hosted, per-customer product needs to actually reach a restaurant's infrastructure. `bootstrap/` makes the app runnable in one process; it does not make it deployable at scale.
2. **n8n orchestration.** The backend was built with n8n specifically in mind as the pipeline sequencer — every stage exists as an independent, callable endpoint precisely so n8n can call them in order and handle retries/checkpoints. The actual n8n workflows connecting those endpoints together were deliberately never generated as part of this build.
3. **The frontend.** Every screen a restaurant's staff would actually use — the dashboard, the discrepancy explorer, the AI summary, the upload flow — was designed in Phase 2 but never built. The API surface exists in full; nothing renders it yet.

None of these three are missing by oversight. Each was named as a distinct, later phase from the beginning, and the engine was deliberately built to be ready for all three without requiring rework: real REST endpoints for n8n to call, a stable API contract for a frontend to consume, and a single composed application for deployment tooling to package.
