"""
Identity & Access Module

Owns the real User entity, password authentication, session issuance and
validation, and branch-level access grants. This is the module every
router's get_current_user_id() placeholder has been waiting on since Data
Import, and the module that produces a real
modules.imports.dependencies.AuthContext instead of the InMemory stand-in
every module's tests have used so far.

Passwords are hashed with salted PBKDF2-HMAC-SHA256 (stdlib only, no new
dependency) - never stored or logged in plaintext. Sessions are opaque,
random, server-side tokens (not JWTs): only a SHA-256 hash of the token is
ever persisted, so a database read alone can't produce a usable token, and
revocation is an immediate database write rather than needing a token
blocklist alongside a stateless scheme.

Explicitly out of scope:
- wiring every existing router's dependency-override - that's an
  application-assembly step, not this module's job (see the
  AnalysisOrchestration precedent: it didn't rewrite Data Import's router
  either, it proved compatibility via an integration test)
- owning Organization/Branch data - references branch_id, doesn't manage it
- invite flows, password reset, MFA - flagged as future extensions, not MVP scope
"""
