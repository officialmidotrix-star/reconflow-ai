"""
Integration seam for the Data Normalization module.

Two things don't exist yet as real modules: knowing a branch's timezone
and default currency (owned by Organization & Branch Management, once
built), and Audit Logging. Both follow the same Protocol + in-memory-stub
pattern used by every module so far.
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


class InMemoryAnalysisTimezoneLookup:
    """Not production code - stands in for Organization & Branch
    Management until that module is built."""

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
