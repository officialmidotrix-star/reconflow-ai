"""
AI Insights Module

Generates a plain-language executive summary from discrepancy data
Discrepancy Detection already computed. The AI never computes anything and
never sees raw transaction data or PII - only aggregate figures (counts by
severity, totals by category). Every number in the generated text is
checked against those input figures after generation; anything the model
introduces that wasn't given to it is rejected, not silently shown.

Explicitly out of scope:
- any computation of matches, comparisons, or discrepancies - reads them, doesn't create them
- editing underlying data
- deciding severity or categorization
"""
