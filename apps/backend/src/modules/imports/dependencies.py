"""
Integration seams for the Data Import module.

This module depends on two things it does not own:
  - knowing whether an analysis exists and is accepting uploads
    (owned by the Analysis Orchestration module, now real - see the
    AnalysisStatus import below), and
  - writing audit trail entries (owned by the Audit Logging module,
    still stubbed - that module hasn't been built yet).

AnalysisStatus used to be defined locally here as a stand-in, since
Analysis Orchestration didn't exist. Now that it does, this re-exports the
canonical definition instead of keeping a second copy that happens to
share string values - AnalysisLookup itself stays a Protocol here (Data
Import only needs to know the shape it can call), and
AnalysisOrchestrationService satisfies it directly, as demonstrated in
that module's own test suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from modules.analysis_orchestration.models import AnalysisStatus

__all__ = [
    "AnalysisStatus",
    "UPLOAD_ACCEPTING_STATES",
    "AnalysisLookup",
    "AuditLogger",
    "AuthContext",
    "InMemoryAnalysisLookup",
    "InMemoryAuditLogger",
]


UPLOAD_ACCEPTING_STATES = frozenset({AnalysisStatus.DRAFT, AnalysisStatus.AWAITING_FILES})


class AnalysisLookup(Protocol):
    def get_status(self, analysis_id: str) -> AnalysisStatus | None:
        """Return the analysis's current status, or None if it doesn't exist."""
        ...

    def get_branch_id(self, analysis_id: str) -> str | None: ...


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
class AuthContext:
    """Represents the authenticated caller. In the full application this is
    produced by the Identity & Access module's auth middleware; here it is
    accepted as a plain value the API layer supplies."""

    user_id: str
    accessible_branch_ids: frozenset[str]

    def can_access_branch(self, branch_id: str) -> bool:
        return branch_id in self.accessible_branch_ids


# --- In-memory stand-ins for local development and tests only ------------


class InMemoryAnalysisLookup:
    """Not production code. Lets Data Import be developed and tested before
    the real Analysis Orchestration module exists."""

    def __init__(self) -> None:
        self._analyses: dict[str, tuple[AnalysisStatus, str]] = {}

    def register(self, analysis_id: str, status: AnalysisStatus, branch_id: str) -> None:
        self._analyses[analysis_id] = (status, branch_id)

    def get_status(self, analysis_id: str) -> AnalysisStatus | None:
        entry = self._analyses.get(analysis_id)
        return entry[0] if entry else None

    def get_branch_id(self, analysis_id: str) -> str | None:
        entry = self._analyses.get(analysis_id)
        return entry[1] if entry else None


@dataclass
class AuditRecord:
    event: str
    user_id: str
    analysis_id: str | None
    metadata: dict
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryAuditLogger:
    """Not production code. Captures audit events in a list for assertions
    in tests instead of writing to the real Audit Logging module."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def log(self, *, event: str, user_id: str, analysis_id: str | None, metadata: dict) -> None:
        self.records.append(
            AuditRecord(event=event, user_id=user_id, analysis_id=analysis_id, metadata=metadata)
        )
