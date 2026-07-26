"""
HTTP layer for the Data Normalization module.

Deliberately thin, same pattern as the imports and validation routers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import NormalizationError_
from .schemas import NormalizationRunResponse
from .service import NormalizationService

router = APIRouter(
    prefix="/uploaded-files/{uploaded_file_id}/normalization", tags=["data-normalization"]
)


def get_normalization_service() -> NormalizationService:
    """Real wiring assembled at application start-up, same pattern as the
    other modules' routers."""
    raise NotImplementedError(
        "Wire up NormalizationService (db session, storage, tz_lookup, "
        "audit_logger) at application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("", response_model=NormalizationRunResponse, status_code=201)
async def normalize_uploaded_file(
    uploaded_file_id: str,
    requested_by: str = Depends(get_current_user_id),
    service: NormalizationService = Depends(get_normalization_service),
) -> NormalizationRunResponse:
    try:
        record = service.normalize_file(uploaded_file_id=uploaded_file_id, requested_by=requested_by)
    except NormalizationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"error_code": exc.error_code, "message": exc.message},
        ) from exc

    return NormalizationRunResponse.model_validate(record)
