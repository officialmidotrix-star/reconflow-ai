"""
Core business logic for the AI Insights module.

`AIInsightService.generate_insight` gathers aggregate-only facts from
Discrepancy Detection's output, calls the configured AIProvider, and
verifies the result before persisting it: every number appearing in the
generated text must be traceable back to a number in the input facts. If
the provider introduces a figure that isn't grounded in what it was given,
the summary is rejected outright - GroundingViolationError, not a
silently-shown hallucination.
"""

from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from modules.discrepancies.models import Discrepancy, Severity

from .dependencies import AIProvider, AnalysisFacts, AuditLogger
from .exceptions import AIInsightPersistError, AIProviderError, GroundingViolationError
from .models import AIInsight

_NUMBER_PATTERN = re.compile(r"\d[\d,]*\.?\d*")


def _extract_numbers(text: str) -> set[str]:
    return {match.group().replace(",", "") for match in _NUMBER_PATTERN.finditer(text)}


def _allowed_numbers(facts: AnalysisFacts) -> set[str]:
    allowed: set[str] = {
        str(facts.total_discrepancies),
        str(facts.critical_count),
        str(facts.high_count),
        str(facts.medium_count),
        str(facts.low_count),
    }
    for value in (facts.total_estimated_loss, *facts.category_loss_breakdown.values()):
        allowed.add(str(value))
        allowed.add(f"{value:.2f}")
        allowed.add(f"{value:.0f}")
    for count in facts.category_breakdown.values():
        allowed.add(str(count))
    return allowed


class AIInsightService:
    def __init__(self, *, db: Session, ai_provider: AIProvider, audit_logger: AuditLogger) -> None:
        self._db = db
        self._ai_provider = ai_provider
        self._audit_logger = audit_logger

    def generate_insight(self, *, analysis_id: str, requested_by: str) -> AIInsight:
        facts = self._gather_facts(analysis_id)

        try:
            summary_text = self._ai_provider.generate_summary(facts)
        except Exception as exc:  # noqa: BLE001
            raise AIProviderError(
                "We couldn't generate an executive summary right now. Please try again."
            ) from exc

        self._validate_grounding(summary_text, facts)

        return self._persist(analysis_id, summary_text, facts, requested_by)

    # -- internal steps -------------------------------------------------

    def _gather_facts(self, analysis_id: str) -> AnalysisFacts:
        discrepancies = self._db.execute(
            select(Discrepancy).where(Discrepancy.analysis_id == analysis_id)
        ).scalars().all()

        category_breakdown: dict[str, int] = {}
        category_loss_breakdown: dict[str, Decimal] = {}
        critical = high = medium = low = 0
        total_loss = Decimal("0.00")

        for d in discrepancies:
            category = d.category.value
            category_breakdown[category] = category_breakdown.get(category, 0) + 1
            category_loss_breakdown[category] = (
                category_loss_breakdown.get(category, Decimal("0.00")) + d.estimated_loss
            )
            total_loss += d.estimated_loss
            if d.severity == Severity.CRITICAL:
                critical += 1
            elif d.severity == Severity.HIGH:
                high += 1
            elif d.severity == Severity.MEDIUM:
                medium += 1
            else:
                low += 1

        return AnalysisFacts(
            total_discrepancies=len(discrepancies),
            critical_count=critical,
            high_count=high,
            medium_count=medium,
            low_count=low,
            total_estimated_loss=total_loss,
            category_breakdown=category_breakdown,
            category_loss_breakdown=category_loss_breakdown,
        )

    def _validate_grounding(self, summary_text: str, facts: AnalysisFacts) -> None:
        found = _extract_numbers(summary_text)
        allowed = _allowed_numbers(facts)
        ungrounded = found - allowed
        if ungrounded:
            raise GroundingViolationError(
                "The generated summary referenced figures that weren't in the "
                f"computed data ({', '.join(sorted(ungrounded))}) and was rejected "
                "rather than shown."
            )

    def _persist(
        self, analysis_id: str, summary_text: str, facts: AnalysisFacts, requested_by: str
    ) -> AIInsight:
        # Supersede: replace any prior insight for this analysis. Nothing
        # else references ai_insights.id as a foreign key, so a hard
        # delete-and-replace is safe here, unlike ReconciliationMatch.
        self._db.execute(delete(AIInsight).where(AIInsight.analysis_id == analysis_id))

        insight = AIInsight(
            analysis_id=analysis_id,
            executive_summary=summary_text,
            provider_name=self._ai_provider.provider_name,
            model_name=getattr(self._ai_provider, "model_name", None),
        )
        self._db.add(insight)

        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise AIInsightPersistError(
                "We couldn't save the executive summary. Please try again."
            ) from exc
        self._db.refresh(insight)

        self._audit_logger.log(
            event="ai_insight_generated",
            user_id=requested_by,
            analysis_id=analysis_id,
            metadata={
                "provider_name": insight.provider_name,
                "total_discrepancies": facts.total_discrepancies,
            },
        )
        return insight
