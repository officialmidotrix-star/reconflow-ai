"""
Core business logic for the Analysis Orchestration module.

The state machine is deliberately small: AWAITING_FILES -> PROCESSING ->
{COMPLETED, FAILED}, plus versioning from either terminal state back to a
fresh AWAITING_FILES. `get_status` and `get_branch_id` exist specifically
so an AnalysisOrchestrationService instance can be passed anywhere
modules.imports.dependencies.AnalysisLookup is expected - the protocol
Data Import declared long before this module existed to satisfy it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from .dependencies import AuditLogger
from .exceptions import AnalysisNotFoundError, AnalysisPersistError, InvalidStatusTransitionError
from .models import Analysis, AnalysisStatus

_ALLOWED_TRANSITIONS: dict[AnalysisStatus, frozenset[AnalysisStatus]] = {
    AnalysisStatus.DRAFT: frozenset({AnalysisStatus.AWAITING_FILES}),
    AnalysisStatus.AWAITING_FILES: frozenset({AnalysisStatus.PROCESSING}),
    AnalysisStatus.PROCESSING: frozenset({AnalysisStatus.COMPLETED, AnalysisStatus.FAILED}),
    AnalysisStatus.COMPLETED: frozenset(),
    AnalysisStatus.FAILED: frozenset(),
}

_VERSIONABLE_FROM = frozenset({AnalysisStatus.COMPLETED, AnalysisStatus.FAILED})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisOrchestrationService:
    def __init__(self, *, db: Session, audit_logger: AuditLogger) -> None:
        self._db = db
        self._audit_logger = audit_logger

    def create_analysis(
        self, *, branch_id: str, created_by: str, period_start: date, period_end: date
    ) -> Analysis:
        analysis = Analysis(
            branch_id=branch_id,
            created_by=created_by,
            version=1,
            status=AnalysisStatus.AWAITING_FILES,
            period_start=period_start,
            period_end=period_end,
        )
        self._db.add(analysis)
        self._commit()
        self._db.refresh(analysis)

        self._audit_logger.log(
            event="analysis_created",
            user_id=created_by,
            analysis_id=analysis.id,
            metadata={"branch_id": branch_id, "version": 1},
        )
        return analysis

    def create_new_version(self, *, previous_analysis_id: str, requested_by: str) -> Analysis:
        previous = self._get_or_raise(previous_analysis_id)
        if previous.status not in _VERSIONABLE_FROM:
            raise InvalidStatusTransitionError(
                "A new version can only be created from a completed or failed analysis."
            )

        new_analysis = Analysis(
            branch_id=previous.branch_id,
            created_by=requested_by,
            parent_analysis_id=previous.id,
            version=previous.version + 1,
            status=AnalysisStatus.AWAITING_FILES,
            period_start=previous.period_start,
            period_end=previous.period_end,
        )
        self._db.add(new_analysis)
        self._commit()
        self._db.refresh(new_analysis)

        self._audit_logger.log(
            event="analysis_new_version_created",
            user_id=requested_by,
            analysis_id=new_analysis.id,
            metadata={"previous_analysis_id": previous.id, "version": new_analysis.version},
        )
        return new_analysis

    def mark_processing(self, *, analysis_id: str, requested_by: str) -> Analysis:
        return self._transition(
            analysis_id, to_status=AnalysisStatus.PROCESSING, requested_by=requested_by,
            event="analysis_marked_processing",
        )

    def mark_completed(self, *, analysis_id: str, requested_by: str) -> Analysis:
        return self._transition(
            analysis_id, to_status=AnalysisStatus.COMPLETED, requested_by=requested_by,
            event="analysis_marked_completed",
        )

    def mark_failed(self, *, analysis_id: str, reason: str, requested_by: str) -> Analysis:
        analysis = self._get_or_raise(analysis_id)
        self._check_transition_allowed(analysis.status, AnalysisStatus.FAILED)
        analysis.status = AnalysisStatus.FAILED
        analysis.failure_reason = reason
        analysis.updated_at = _utcnow()
        self._commit()
        self._db.refresh(analysis)

        self._audit_logger.log(
            event="analysis_marked_failed",
            user_id=requested_by,
            analysis_id=analysis_id,
            metadata={"reason": reason},
        )
        return analysis

    def get_analysis(self, analysis_id: str) -> Analysis:
        return self._get_or_raise(analysis_id)

    # -- satisfies modules.imports.dependencies.AnalysisLookup -----------

    def get_status(self, analysis_id: str) -> AnalysisStatus | None:
        analysis = self._db.get(Analysis, analysis_id)
        return analysis.status if analysis else None

    def get_branch_id(self, analysis_id: str) -> str | None:
        analysis = self._db.get(Analysis, analysis_id)
        return analysis.branch_id if analysis else None

    # -- internal steps ---------------------------------------------------

    def _transition(
        self, analysis_id: str, *, to_status: AnalysisStatus, requested_by: str, event: str
    ) -> Analysis:
        analysis = self._get_or_raise(analysis_id)
        self._check_transition_allowed(analysis.status, to_status)
        analysis.status = to_status
        analysis.updated_at = _utcnow()
        self._commit()
        self._db.refresh(analysis)

        self._audit_logger.log(
            event=event, user_id=requested_by, analysis_id=analysis_id, metadata={}
        )
        return analysis

    def _get_or_raise(self, analysis_id: str) -> Analysis:
        analysis = self._db.get(Analysis, analysis_id)
        if analysis is None:
            raise AnalysisNotFoundError("We couldn't find that analysis.")
        return analysis

    def _check_transition_allowed(self, current: AnalysisStatus, target: AnalysisStatus) -> None:
        if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
            raise InvalidStatusTransitionError(
                f"Can't move an analysis from {current.value} to {target.value}."
            )

    def _commit(self) -> None:
        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise AnalysisPersistError("We couldn't save that. Please try again.") from exc
