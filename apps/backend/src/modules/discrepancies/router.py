"""
HTTP layer for the Discrepancy Detection & Classification module.

Deliberately thin, same pattern as every other module's router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import DiscrepancyError_
from .schemas import ConfirmCancellationRequest, DiscrepancyResponse, DiscrepancyRunResponse
from .service import DiscrepancyService

router = APIRouter(prefix="/analyses/{analysis_id}/discrepancies", tags=["discrepancy-detection"])
# A second router for the one route that hangs off a single discrepancy rather
# than off an analysis; included alongside `router` at application start-up.
cancellation_router = APIRouter(tags=["discrepancy-detection"])


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
def detect_discrepancies(
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


@cancellation_router.post(
    "/discrepancies/{discrepancy_id}/cancellation", response_model=DiscrepancyResponse
)
def confirm_cancellation(
    discrepancy_id: str,
    body: ConfirmCancellationRequest,
    requested_by: str = Depends(get_current_user_id),
    service: DiscrepancyService = Depends(get_discrepancy_service),
) -> DiscrepancyResponse:
    """Say who cancelled an order that was cancelled after preparation.
    CUSTOMER or PLATFORM makes it claimable (net of commission); RESTAURANT
    makes it not claimable."""
    try:
        d = service.confirm_cancellation(
            discrepancy_id=discrepancy_id, cancelled_by=body.cancelled_by, requested_by=requested_by
        )
    except DiscrepancyError_ as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"error_code": exc.error_code, "message": exc.message},
        ) from exc
    return DiscrepancyResponse.model_validate(d)
