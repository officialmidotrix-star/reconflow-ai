"""
HTTP layer for the Audit Logging module.

No POST endpoint - see package docstring. Entries only ever come from
other modules' direct service calls (dependency injection), never from an
external request.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .schemas import AuditLogEntryResponse
from .service import AuditLogService

router = APIRouter(prefix="/audit-log", tags=["audit-logging"])


def get_audit_log_service() -> AuditLogService:
    """Real wiring assembled at application start-up. This is also the
    single instance that should be passed as the audit_logger dependency
    to every other module's service constructor - it satisfies all of
    their identical AuditLogger protocols at once."""
    raise NotImplementedError(
        "Wire up AuditLogService (db session) at application start-up "
        "and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder wired to Identity & Access's real get_current_user_id
    at application start-up, same pattern as every other module's router."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.get("", response_model=list[AuditLogEntryResponse])
async def list_audit_log(
    analysis_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    event: str | None = Query(default=None),
    limit: int = Query(default=100, le=1000),
    _requested_by: str = Depends(get_current_user_id),
    service: AuditLogService = Depends(get_audit_log_service),
) -> list[AuditLogEntryResponse]:
    entries = service.list_entries(
        analysis_id=analysis_id, user_id=user_id, event=event, limit=limit
    )
    return [AuditLogEntryResponse.model_validate(e) for e in entries]
