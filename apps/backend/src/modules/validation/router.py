"""
HTTP layer for the Data Validation module.

Deliberately thin, same pattern as modules/imports/router.py: parse the
request, call ValidationService, translate the result. No business logic
lives here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import ValidationError_
from .schemas import FileValidationResponse
from .service import ValidationService

router = APIRouter(prefix="/uploaded-files/{uploaded_file_id}/validation", tags=["data-validation"])


def get_validation_service() -> ValidationService:
    """Real wiring (DB session, the real storage backend, and the real
    Audit Logging implementation once it exists) is assembled at
    application start-up, same as get_import_service() in the imports
    module's router."""
    raise NotImplementedError(
        "Wire up ValidationService (db session, storage, audit_logger) at "
        "application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("", response_model=FileValidationResponse, status_code=201)
async def validate_uploaded_file(
    uploaded_file_id: str,
    requested_by: str = Depends(get_current_user_id),
    service: ValidationService = Depends(get_validation_service),
) -> FileValidationResponse:
    try:
        record = service.validate_file(uploaded_file_id=uploaded_file_id, requested_by=requested_by)
    except ValidationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"error_code": exc.error_code, "message": exc.message},
        ) from exc

    return FileValidationResponse.model_validate(record)
