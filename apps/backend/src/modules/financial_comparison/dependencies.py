"""
Integration seam for the Financial Comparison module.

ContractLookup stands in for Reference & Contract Configuration, which
doesn't exist yet. It is deliberately keyed by analysis_id rather than
branch_id, following the same precedent Data Normalization already set
with AnalysisTimezoneLookup: nothing in the schema persists a branch_id
anywhere yet (Organization & Branch Management doesn't exist either), so
every stub so far resolves "the org/branch context for this analysis"
directly instead of modeling a branch entity prematurely. It's also not
yet keyed by delivery platform - Transaction doesn't carry a platform
identifier (Data Import only distinguishes POS_EXPORT vs
PLATFORM_SETTLEMENT), and Data Import's one-active-file-per-slot design
means at most one platform's settlement file is active per analysis
anyway. A platform dimension belongs here once Reference & Contract
Configuration, and multi-platform-per-analysis support, actually exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol


class ContractLookup(Protocol):
    def get_commission_rate(self, *, analysis_id: str, as_of: datetime) -> Decimal | None:
        """Effective commission rate (e.g. Decimal('0.15') for 15%) for this
        analysis as of the given date, or None if no contract is configured."""
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


class InMemoryContractLookup:
    """Not production code - stands in for Reference & Contract
    Configuration until that module is built. Supports temporal contracts
    (valid_from/valid_to) to mirror the Phase 2 database design's intent,
    even though nothing yet builds a UI or API around changing rates."""

    def __init__(self) -> None:
        self._contracts: list[tuple[str, datetime, datetime | None, Decimal]] = []

    def register(
        self,
        analysis_id: str,
        rate: Decimal,
        *,
        valid_from: datetime,
        valid_to: datetime | None = None,
    ) -> None:
        self._contracts.append((analysis_id, valid_from, valid_to, rate))

    def get_commission_rate(self, *, analysis_id: str, as_of: datetime) -> Decimal | None:
        for a_id, valid_from, valid_to, rate in self._contracts:
            if a_id != analysis_id:
                continue
            if as_of < valid_from:
                continue
            if valid_to is not None and as_of >= valid_to:
                continue
            return rate
        return None


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
