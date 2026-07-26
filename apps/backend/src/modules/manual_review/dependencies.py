"""
Integration seam for the Manual Review & Override module.

Only Audit Logging doesn't exist yet - same Protocol + in-memory-stub
pattern as every module so far. Everything else this module needs
(ReconciliationMatch, Discrepancy, Transaction) is read from real tables,
imported directly in service.py.
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
