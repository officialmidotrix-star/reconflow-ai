"""
HTTP layer for the Matching Engine module.

Deliberately thin, same pattern as every other module's router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import MatchingError_
from .schemas import MatchingRunResponse
from .service import MatchingService

router = APIRouter(prefix="/analyses/{analysis_id}/matching", tags=["matching-engine"])


def get_matching_service() -> MatchingService:
    """Real wiring assembled at application start-up, same pattern as the
    other modules' routers."""
    raise NotImplementedError(
        "Wire up MatchingService (db session, audit_logger) at application "
        "start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("", response_model=MatchingRunResponse, status_code=201)
async def run_matching(
    analysis_id: str,
    requested_by: str = Depends(get_current_user_id),
    service: MatchingService = Depends(get_matching_service),
) -> MatchingRunResponse:
    try:
        record = service.run_matching(analysis_id=analysis_id, requested_by=requested_by)
    except MatchingError_ as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"error_code": exc.error_code, "message": exc.message},
        ) from exc

    return MatchingRunResponse.model_validate(record)
