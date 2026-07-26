"""
Integration seam for the AI Insights module.

AIProvider is different in kind from every other stub in this codebase:
it doesn't stand in for a not-yet-built ReconFlow module - it's a
genuinely external system (an LLM), abstracted the same way regardless,
per the Phase 1 decision that the provider must be swappable (cloud today,
self-hosted later) without touching calling code. The real implementation
lives in providers/ (per the Phase 2 folder structure, which reserved
exactly that subfolder for "adapter implementations, cloud/self-hosted").
FakeAIProvider here is test infrastructure only, same as every other
module's InMemory doubles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class AnalysisFacts:
    """The complete, and only, information an AIProvider is given. Deliberately
    aggregate-only - no transaction rows, no customer names or phone numbers,
    no order references. Per the Phase 1 decision that a cloud provider may
    receive computed figures but never raw PII."""

    total_discrepancies: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    total_estimated_loss: Decimal
    category_breakdown: dict[str, int]
    category_loss_breakdown: dict[str, Decimal]


class AIProvider(Protocol):
    provider_name: str

    def generate_summary(self, facts: AnalysisFacts) -> str:
        """Return narrative text describing the given facts. Implementations
        must not introduce figures beyond what's in `facts` - the calling
        service enforces this after the fact regardless, but a well-behaved
        provider's prompt should already say so."""
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


class FakeAIProvider:
    """Not production code - deterministic, template-based text for tests.
    `inject_ungrounded_number` simulates a provider hallucinating a figure
    that wasn't in the input, to exercise the grounding-violation path."""

    provider_name = "fake"
    model_name = "fake-template-v1"

    def __init__(self, *, inject_ungrounded_number: bool = False) -> None:
        self._inject_ungrounded_number = inject_ungrounded_number

    def generate_summary(self, facts: AnalysisFacts) -> str:
        if facts.total_discrepancies == 0:
            text = "This analysis found no discrepancies - everything reconciled cleanly."
        else:
            text = (
                f"This analysis found {facts.total_discrepancies} discrepancies, "
                f"with an estimated total impact of {facts.total_estimated_loss}. "
                f"Of these, {facts.critical_count} are critical and {facts.high_count} are high severity."
            )
        if self._inject_ungrounded_number:
            text += " That's roughly 42% higher than last month."
        return text


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
