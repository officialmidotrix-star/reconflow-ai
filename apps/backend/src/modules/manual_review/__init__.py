"""
Manual Review & Override Module

The human-in-the-loop layer: lets an accountant confirm or reject a
ReconciliationMatch, acknowledge or dispute a Discrepancy, or manually pair
two transactions the algorithm missed. Never recomputes anything - it lays
an audited human judgment alongside what Matching, Comparison, and
Discrepancy Detection already produced. Every decision is its own
permanent row; re-reviewing something is a new record, not a correction
in place.

Deliberate exception to "each module writes only the tables it owns":
manual pairing writes to reconciliation_matches, which Matching Engine
owns. Folding match-creation into Matching Engine would tangle a
human-judgment concern into a deterministic-algorithm module - a worse
coupling than the one it would avoid. Treated as a narrow, considered
exception, not a precedent.

Explicitly out of scope:
- recomputing matches, comparisons, or discrepancies
- editing a Discrepancy's category, severity, or estimated loss
- AI involvement, user/role management
"""
