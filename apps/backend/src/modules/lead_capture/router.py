from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import LeadCaptureError_
from .schemas import LeadResponse, RecordLeadRequest
from .service import LeadCaptureService

router = APIRouter(prefix="/analyses", tags=["lead-capture"])


def get_lead_capture_service() -> LeadCaptureService:
    raise NotImplementedError(
        "Wire up LeadCaptureService (db session) at application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("/{analysis_id}/lead", response_model=LeadResponse, status_code=201)
async def record_lead(
    analysis_id: str,
    body: RecordLeadRequest,
    _requested_by: str = Depends(get_current_user_id),
    service: LeadCaptureService = Depends(get_lead_capture_service),
) -> LeadResponse:
    try:
        return service.record_lead(analysis_id, body)
    except LeadCaptureError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
