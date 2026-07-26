"""
Notification Module

Sends a completion alert (success or failure) for an analysis through a
swappable channel - email for MVP, other channels later, same adapter
pattern as AI Insights' AIProvider. Composes the message from Analysis
Orchestration's real data (read-only); doesn't decide when to notify -
that's n8n calling this module's endpoint at the right pipeline
checkpoint, same as every other module.

Every attempt (sent or failed) is recorded as its own permanent row, same
reasoning as Reporting & Export's reports: a notification log is
historical by nature, never superseded. A channel failure is recorded as
a FAILED notification, not raised as an exception - the pipeline
shouldn't halt because an email didn't go through, but the failure still
needs to be visible.

Explicitly out of scope:
- deciding when to notify - n8n's job
- template management UI, per-user channel preferences -> future extension
- retry/queueing infrastructure - MVP is one best-effort attempt, recorded either way
"""
