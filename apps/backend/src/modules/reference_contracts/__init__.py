"""
Reference & Contract Configuration Module

Owns the commission contract data Financial Comparison has been reading
through InMemoryContractLookup since it was built - the "what SHOULD the
commission be" source of truth flagged as a structural gap back in Phase
1. Provides the real implementation of Financial Comparison's
ContractLookup protocol shape. See this module's own test suite for an
integration test proving that directly against a real ComparisonService
call.

CommissionContract is temporally versioned (valid_from/valid_to), exactly
as the Phase 2 database design specified: updating a rate never corrupts
a past analysis that should reconcile against the rate that was actually
in effect at the time.

Explicitly out of scope:
- branch/organization management -> modules/organizations (already built)
- role enforcement on who may configure contracts - a caller applies
  Identity & Access's ensure_role, not this module's job
- resolving which platform an analysis's settlement file is from - a
  pre-existing gap in Data Import/Normalization, not solved here (see the
  ambiguous-contract handling in service.py for how this module works
  around it rather than pretending to fix it)
"""
