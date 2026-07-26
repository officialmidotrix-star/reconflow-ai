"""
Audit Logging Module

Owns the real, persistent audit trail every one of the other 14 modules
has been writing to its own local InMemoryAuditLogger stand-in since Data
Import. Every one of those AuditLogger protocols is structurally
identical - this single implementation satisfies all of them at once,
not one-at-a-time the way prior foundational modules closed a single
protocol each.

The one true leaf in the dependency graph: no dependencies.py, because
this module consumes nothing and stands in for nothing else.

log() never raises, even on a persistence failure - none of the 14
existing call sites wrap it in a try/except, having been built assuming
it's fire-and-forget. A failure is caught, rolled back, and written to
stderr as a last-resort visibility mechanism, not propagated to the
caller's business operation.

Explicitly out of scope:
- a public "create entry" endpoint - entries only come from other
  modules' direct service calls, never an external request
- retention/archival policy - flagged as a future consideration, not MVP scope
"""
