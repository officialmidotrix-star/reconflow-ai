"""
Integration seam for the Discrepancy Detection & Classification module.

The only thing that doesn't exist yet is Audit Logging - same Protocol +
in-memory-stub pattern as every module so far. Unlike every module since
Data Import, this one needs no other stub: everything it requires already
lives on the three real upstream tables (ReconciliationMatch, Transaction,
ComparisonResult). Structurally the least coupled module built so far.
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
