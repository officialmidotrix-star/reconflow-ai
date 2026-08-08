"""
Integration seam for the Matching Engine module.

AnalysisTimezoneLookup added alongside AuditLogger, once the day-window
fallback pass turned out to need branch-local dates, not the UTC dates
occurred_at is stored as - duplicated from Normalization's identical
protocol rather than imported from it, same reasoning as AuditLogger
already being independently defined in both: each module stays fully
self-contained even where two modules happen to need the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


class AnalysisTimezoneLookup(Protocol):
    def get_timezone(self, analysis_id: str) -> str | None:
        """IANA timezone name (e.g. 'Asia/Riyadh'), or None if not configured."""
        ...

    def get_currency(self, analysis_id: str) -> str | None:
        """ISO 4217 currency code (e.g. 'SAR'), or None if not configured."""
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


@dataclass
class AuditRecord:
    event: str
    user_id: str
    analysis_id: str | None
    metadata: dict
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryAuditLogger:
    """Not production code - same pattern as the other modules."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def log(self, *, event: str, user_id: str, analysis_id: str | None, metadata: dict) -> None:
        self.records.append(
            AuditRecord(event=event, user_id=user_id, analysis_id=analysis_id, metadata=metadata)
        )


class InMemoryAnalysisTimezoneLookup:
    """Not production code - same stand-in pattern as Normalization's own,
    until Organization & Branch Management is queried directly here too."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, str]] = {}

    def register(self, analysis_id: str, timezone_name: str, currency_code: str) -> None:
        self._data[analysis_id] = (timezone_name, currency_code)

    def get_timezone(self, analysis_id: str) -> str | None:
        entry = self._data.get(analysis_id)
        return entry[0] if entry else None

    def get_currency(self, analysis_id: str) -> str | None:
        entry = self._data.get(analysis_id)
        return entry[1] if entry else None
