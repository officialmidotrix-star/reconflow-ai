"""
Integration seam for the Notification module.

NotificationChannel is different in kind from most stubs in this codebase,
same as AI Insights' AIProvider: it doesn't stand in for a not-yet-built
ReconFlow module, it's a genuinely external system (an SMTP server today,
other channels later), abstracted so it's swappable without touching
calling code. The real implementation lives in channels/ (mirroring AI
Insights' providers/ subfolder). FakeNotificationChannel here is test
infrastructure only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


class NotificationChannel(Protocol):
    channel_name: str

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        """Send the message. Raise on failure - the calling service
        catches it and records a FAILED notification rather than letting
        the exception propagate."""
        ...


class AuditLogger(Protocol):
    def log(
        self,
        *,
        event: str,
        user_id: str,
        analysis_id: str | None,
        metadata: dict,
    ) -> None: ...


class FakeNotificationChannel:
    """Not production code - deterministic for tests. Set `should_fail` to
    simulate a channel failure and exercise the FAILED-notification path."""

    channel_name = "fake"

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.sent: list[tuple[str, str, str]] = []  # (recipient, subject, body)

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        if self.should_fail:
            raise ConnectionError("simulated channel failure")
        self.sent.append((recipient, subject, body))


@dataclass
class AuditRecord:
    event: str
    user_id: str
    analysis_id: str | None
    metadata: dict
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryAuditLogger:
    """Not production code - same pattern as every other module."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def log(self, *, event: str, user_id: str, analysis_id: str | None, metadata: dict) -> None:
        self.records.append(
            AuditRecord(event=event, user_id=user_id, analysis_id=analysis_id, metadata=metadata)
        )
