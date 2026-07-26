"""
Financial Comparison Module

For each fully-matched ReconciliationMatch, checks whether the settlement
is financially correct against the branch's contracted terms: was the
commission the contracted rate, does the platform's gross settlement match
the POS order value. Says *that* something is off and by how much - not
*why* or how severe, and not what to do about it.

Explicitly out of scope (see module boundaries in design doc):
- root-cause categorization and severity -> modules/discrepancies
- manual override                        -> modules/manual_review
- AI narrative                           -> modules/ai_insights
- re-matching or judging Matching Engine's output - trusted as-is
- owning contract data - reads it, doesn't manage it
"""
