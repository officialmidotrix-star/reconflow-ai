"""
Organization & Branch Management Module

Owns the real Organization and Branch entities that nearly every
branch-touching table has been forward-referencing as a bare stub since
Data Import (Analysis.branch_id, UserBranchAccess.branch_id). Also
provides the real implementation of the shape Normalization's
AnalysisTimezoneLookup protocol expects - see this module's own test
suite for an integration test proving that directly.

Organization is a deliberate singleton: one deployment serves exactly one
customer (the decision made back in Phase 2), so creating a second
organization is rejected the same way Data Import rejects a second active
file in one upload slot.

Explicitly out of scope:
- commission/contract data -> modules/reference_contract_configuration (not built yet)
- user/role management -> modules/identity_access (already built)
- multi-organization support - the singleton constraint is deliberate, not a gap
- temporal/versioned timezone tracking - unlike commission rates, a
  branch's timezone essentially never changes, so there's no real use
  case for the same kind of history CommissionContract will need
"""
