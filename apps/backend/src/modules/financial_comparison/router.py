"""
HTTP layer for the Financial Comparison module.

Deliberately thin, same pattern as every other module's router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import ComparisonError_
from .schemas import ComparisonRunResponse
from .service import ComparisonService

router = APIRouter(prefix="/analyses/{analysis_id}/comparison", tags=["financial-comparison"])


def get_comparison_service() -> ComparisonService:
    """Real wiring assembled at application start-up, same pattern as the
    other modules' routers."""
    raise NotImplementedError(
        "Wire up ComparisonService (db session, contract_lookup, "
        "audit_logger) at application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("", response_model=ComparisonRunResponse, status_code=201)
async def run_comparison(
    analysis_id: str,
    requested_by: str = Depends(get_current_user_id),
    service: ComparisonService = Depends(get_comparison_service),
) -> ComparisonRunResponse:
    try:
        record = service.run_comparison(analysis_id=analysis_id, requested_by=requested_by)
    except ComparisonError_ as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"error_code": exc.error_code, "message": exc.message},
        ) from exc

    return ComparisonRunResponse.model_validate(record)
