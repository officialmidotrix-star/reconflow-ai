"""
HTTP layer for the Deployment, Update & Licensing module.

GET /deployment/info has no auth dependency at all - deliberately, unlike
every other GET in this build. Its purpose is letting support/ops tooling
check version and license status from outside a login session; requiring
one would defeat that. It never returns the raw license_key, only the
computed status, so this stays safe to leave open.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import DeploymentError_
from .schemas import (
    DeploymentInfoResponse,
    RecordLicenseRequest,
    RecordUpdateRequest,
    UpdateEventResponse,
)
from .service import DeploymentService

router = APIRouter(prefix="/deployment", tags=["deployment"])


def get_deployment_service() -> DeploymentService:
    """Real wiring assembled at application start-up, same pattern as
    every other module's router."""
    raise NotImplementedError(
        "Wire up DeploymentService (db session, audit_logger) at "
        "application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder wired to Identity & Access's real get_current_user_id
    at application start-up, same pattern as every other module's router."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


def _to_response(service: DeploymentService, info) -> DeploymentInfoResponse:
    return DeploymentInfoResponse(
        id=info.id,
        current_version=info.current_version,
        license_status=service.get_license_status(),
        license_expires_at=info.license_expires_at,
        installed_at=info.installed_at,
    )


@router.get("/info", response_model=DeploymentInfoResponse)
async def get_deployment_info(
    service: DeploymentService = Depends(get_deployment_service),
) -> DeploymentInfoResponse:
    info = service.get_deployment_info()
    return _to_response(service, info)


@router.post("/license", response_model=DeploymentInfoResponse)
async def record_license(
    body: RecordLicenseRequest,
    requested_by: str = Depends(get_current_user_id),
    service: DeploymentService = Depends(get_deployment_service),
) -> DeploymentInfoResponse:
    try:
        info = service.record_license(
            license_key=body.license_key, expires_at=body.expires_at, requested_by=requested_by
        )
    except DeploymentError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return _to_response(service, info)


@router.post("/updates", response_model=UpdateEventResponse, status_code=201)
async def record_update(
    body: RecordUpdateRequest,
    requested_by: str = Depends(get_current_user_id),
    service: DeploymentService = Depends(get_deployment_service),
) -> UpdateEventResponse:
    try:
        event = service.record_update(
            version=body.version, notes=body.notes, requested_by=requested_by
        )
    except DeploymentError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return UpdateEventResponse.model_validate(event)


@router.get("/updates", response_model=list[UpdateEventResponse])
async def list_updates(
    service: DeploymentService = Depends(get_deployment_service),
) -> list[UpdateEventResponse]:
    return [UpdateEventResponse.model_validate(e) for e in service.list_update_history()]
