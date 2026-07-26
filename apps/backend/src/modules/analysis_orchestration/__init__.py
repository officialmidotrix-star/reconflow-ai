"""
Analysis Orchestration Module

Owns the Analysis entity itself: its lifecycle status, its versioning, and
the state-machine rules for what transition is allowed from where. This is
the module every prior module has been waiting for - Data Import's
AnalysisLookup protocol was written specifically so a real implementation
here could satisfy it later, and every other module's tests have been
stubbing a bare "analyses" table in its place.

Per the Phase 2 architecture, n8n is the actual pipeline sequencer - it
calls each module's endpoint in order (normalize, match, compare,
classify, generate insights) and calls back into this module at the
checkpoints (mark-processing, mark-completed, mark-failed). This module
does NOT call Normalization/Matching/Comparison/Discrepancies/AI Insights
itself - doing so would blur the boundary Phase 2 already established
between "n8n sequences calls" and "backend modules do the work."

Explicitly out of scope:
- calling other modules' business logic directly - that's n8n's job
- owning branch/organization data - reads/references branch_id, doesn't manage it
- owning file or transaction data
"""
