"""
Deployment, Update & Licensing Module

The last of the original 16 modules. Deliberately lighter than the
others, matching the Phase 2 decision that deployment and licensing stay
manual for MVP: this module reports version and license status, and lets
an operator record that an update happened - it doesn't automate updates,
verify license keys cryptographically, or phone home to any license
server. That automation is the explicitly-deferred future piece.

GET /deployment/info is the one deliberately unauthenticated endpoint in
the whole build - its purpose is letting support/ops tooling check
version and license status from outside, which requiring login would
defeat. The response only ever exposes the *computed* license status,
never the raw license key.

Explicitly out of scope:
- license-key generation/verification
- automated update download/apply
- enforcement - nothing in the system refuses to run if unlicensed/expired
"""
