"""
Integration seam for the Identity & Access module.

Only Audit Logging doesn't exist yet - same Protocol + in-memory-stub
pattern as every module so far, including this one, even though this
module is itself what most other modules have been waiting on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


class AuditLogger(Protocol):
    def log(
        self,
        *,
        event: str,
        user_id: str,
        analysis_id: str | None,
        metadata: dict,
    ) -> None: ...


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
