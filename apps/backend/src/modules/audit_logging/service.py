"""
Core logic for the Audit Logging module.

`log()` matches the exact shape every one of the other 14 modules'
AuditLogger protocol declares - this single class satisfies all of them,
since Python's structural typing doesn't care which module wrote the
Protocol, only that the method shape matches. See this module's own test
suite for integration tests proving that against several real modules.

`log()` deliberately never raises - see package docstring for why. A
persistence failure is caught, rolled back, and written to stderr via the
standard logging module as a last-resort visibility mechanism, not
propagated to whatever business operation was being audited.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditLogEntry

_logger = logging.getLogger(__name__)


class AuditLogService:
    def __init__(self, *, db: Session) -> None:
        self._db = db

    def log(
        self,
        *,
        event: str,
        user_id: str,
        analysis_id: str | None,
        metadata: dict,
    ) -> None:
        entry = AuditLogEntry(
            event=event, user_id=user_id, analysis_id=analysis_id, details=metadata or {}
        )
        try:
            self._db.add(entry)
            self._db.commit()
        except Exception:  # noqa: BLE001
            self._db.rollback()
            _logger.error(
                "Failed to persist audit log entry (event=%s, user_id=%s, analysis_id=%s) - "
                "the operation that triggered this continues; only the audit record was lost.",
                event, user_id, analysis_id, exc_info=True,
            )

    def list_entries(
        self,
        *,
        analysis_id: str | None = None,
        user_id: str | None = None,
        event: str | None = None,
        limit: int = 100,
    ) -> list[AuditLogEntry]:
        query = select(AuditLogEntry)
        if analysis_id is not None:
            query = query.where(AuditLogEntry.analysis_id == analysis_id)
        if user_id is not None:
            query = query.where(AuditLogEntry.user_id == user_id)
        if event is not None:
            query = query.where(AuditLogEntry.event == event)
        query = query.order_by(AuditLogEntry.created_at.desc()).limit(limit)
        return self._db.execute(query).scalars().all()
