"""
Reporting & Export Module

Packages an already-completed analysis's discrepancies, headline counts,
and AI executive summary into a downloadable file (CSV or XLSX). Computes
nothing new - reads what Discrepancy Detection, Discrepancy Detection's
own run summary, and AI Insights already produced, and reuses Data
Import's encrypted storage (now shared, see storage/file_storage.py) to
persist the result.

Unlike every "current state" table so far (Comparison, Discrepancies, AI
Insights), reports are NOT superseded on regeneration - each generation is
a kept, historical export artifact (the kind of thing someone might email
to a stakeholder or archive), so every call creates a new row and a new
file rather than replacing the last one.

Explicitly out of scope:
- computing discrepancies, comparisons, or insights - reads them
- delivering the report anywhere (email, Slack) -> modules/notification
- PDF generation - flagged as a future extension, not built this round
  (a real layout-capable PDF library is a heavier dependency than this
  phase's scope calls for; CSV/XLSX cover the same underlying data)
"""
