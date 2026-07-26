"""
HTTP layer for the Discrepancy Detection & Classification module.

Deliberately thin, same pattern as every other module's router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import DiscrepancyError_
from .schemas import DiscrepancyRunResponse
from .service import DiscrepancyService

router = APIRouter(prefix="/analyses/{analysis_id}/discrepancies", tags=["discrepancy-detection"])


def get_discrepancy_service() -> DiscrepancyService:
    """Real wiring assembled at application start-up, same pattern as the
    other modules' routers."""
    raise NotImplementedError(
        "Wire up DiscrepancyService (db session, audit_logger) at "
        "application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("", response_model=DiscrepancyRunResponse, status_code=201)
async def detect_discrepancies(
    analysis_id: str,
    requested_by: str = Depends(get_current_user_id),
    service: DiscrepancyService = Depends(get_discrepancy_service),
) -> DiscrepancyRunResponse:
    try:
        record = service.detect_discrepancies(analysis_id=analysis_id, requested_by=requested_by)
    except DiscrepancyError_ as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"error_code": exc.error_code, "message": exc.message},
        ) from exc

    return DiscrepancyRunResponse.model_validate(record)
