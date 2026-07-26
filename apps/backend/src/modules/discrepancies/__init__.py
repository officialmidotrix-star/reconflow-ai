"""
Discrepancy Detection & Classification Module

Turns the raw signals Matching and Comparison already produced - unmatched
transactions, out-of-tolerance comparisons - into named, categorized
Discrepancy records with a severity and an estimated financial impact.
Assigns a label and a number; does not investigate why, decide what to do,
or write anything a human reads as prose.

Four categories, each traceable to exactly one ReconciliationMatch:
- MISSING_SETTLEMENT       (POS transaction, no platform counterpart)
- UNEXPECTED_SETTLEMENT    (platform transaction, no POS counterpart)
- INCORRECT_COMMISSION     (ComparisonResult, commission out of tolerance)
- SETTLEMENT_AMOUNT_MISMATCH (ComparisonResult, settlement out of tolerance)

A single ComparisonResult failing both checks produces two Discrepancy
rows, not one - matching the Phase 2 database design's own statement that
a matched pair can have zero, one, or multiple discrepancy findings.

Explicitly out of scope (see module boundaries in design doc):
- root-cause investigation (timing vs. export bug vs. fraud) -> not this module's job at all
- AI narrative                                                -> modules/ai_insights
- manual override                                             -> modules/manual_review
- re-judging Matching's or Comparison's output - trusted as-is, read-only
"""
