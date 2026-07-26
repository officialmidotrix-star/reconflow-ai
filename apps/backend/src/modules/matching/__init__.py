"""
Matching Engine Module

Pairs each normalized POS transaction with its corresponding platform
settlement transaction for one analysis, or determines that no such pairing
exists. Produces ReconciliationMatch rows - matched pairs with a confidence
score, or unmatched transactions with a null counterpart - per the Phase 2
ERD. Makes no judgment about whether a matched pair's amounts *should*
agree; only whether two transactions plausibly represent the same order.

Explicitly out of scope (see module boundaries in design doc):
- business-rule comparison (commission, contract terms) -> modules/financial_comparison
- categorizing *why* something is unmatched, severity     -> modules/discrepancies
- manual override of a match                              -> modules/manual_review
"""
