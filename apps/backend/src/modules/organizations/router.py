"""
HTTP layer for the Organization & Branch Management module.

Deliberately thin, same pattern as every other module's router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import OrganizationError_
from .schemas import (
    BranchResponse,
    CreateBranchRequest,
    CreateOrganizationRequest,
    OrganizationResponse,
)
from .service import OrganizationService

router = APIRouter(tags=["organizations"])


def get_organization_service() -> OrganizationService:
    """Real wiring assembled at application start-up, same pattern as
    every other module's router. This is also the instance that should be
    passed wherever modules.normalization.dependencies.AnalysisTimezoneLookup
    is expected in production."""
    raise NotImplementedError(
        "Wire up OrganizationService (db session, audit_logger) at "
        "application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder wired to Identity & Access's real get_current_user_id
    at application start-up, same pattern as every other module's router."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("/organizations", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    body: CreateOrganizationRequest,
    requested_by: str = Depends(get_current_user_id),
    service: OrganizationService = Depends(get_organization_service),
) -> OrganizationResponse:
    try:
        org = service.create_organization(
            legal_name=body.legal_name, default_currency=body.default_currency,
            requested_by=requested_by,
        )
    except OrganizationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return OrganizationResponse.model_validate(org)


@router.get("/organizations/current", response_model=OrganizationResponse | None)
async def get_current_organization(
    service: OrganizationService = Depends(get_organization_service),
) -> OrganizationResponse | None:
    org = service.get_current_organization()
    return OrganizationResponse.model_validate(org) if org else None


@router.post("/branches", response_model=BranchResponse, status_code=201)
async def create_branch(
    body: CreateBranchRequest,
    requested_by: str = Depends(get_current_user_id),
    service: OrganizationService = Depends(get_organization_service),
) -> BranchResponse:
    try:
        branch = service.create_branch(
            organization_id=body.organization_id, name=body.name, timezone=body.timezone,
            requested_by=requested_by,
        )
    except OrganizationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return BranchResponse.model_validate(branch)


@router.get("/branches/{branch_id}", response_model=BranchResponse)
async def get_branch(
    branch_id: str,
    service: OrganizationService = Depends(get_organization_service),
) -> BranchResponse:
    try:
        branch = service.get_branch(branch_id=branch_id)
    except OrganizationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return BranchResponse.model_validate(branch)


@router.get("/organizations/{organization_id}/branches", response_model=list[BranchResponse])
async def list_branches(
    organization_id: str,
    service: OrganizationService = Depends(get_organization_service),
) -> list[BranchResponse]:
    branches = service.list_branches(organization_id=organization_id)
    return [BranchResponse.model_validate(b) for b in branches]
