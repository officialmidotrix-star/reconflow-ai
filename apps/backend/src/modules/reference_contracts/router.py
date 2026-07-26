"""
HTTP layer for the Reference & Contract Configuration module.

Deliberately thin, same pattern as every other module's router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import ReferenceContractError_
from .schemas import (
    ContractResponse,
    CreateContractRequest,
    CreatePlatformRequest,
    PlatformResponse,
)
from .service import ReferenceContractService

router = APIRouter(tags=["reference-contracts"])


def get_reference_contract_service() -> ReferenceContractService:
    """Real wiring assembled at application start-up. This is also the
    instance that should be passed wherever
    modules.financial_comparison.dependencies.ContractLookup is expected
    in production."""
    raise NotImplementedError(
        "Wire up ReferenceContractService (db session, audit_logger) at "
        "application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder wired to Identity & Access's real get_current_user_id
    at application start-up, same pattern as every other module's router."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("/platforms", response_model=PlatformResponse, status_code=201)
async def create_platform(
    body: CreatePlatformRequest,
    requested_by: str = Depends(get_current_user_id),
    service: ReferenceContractService = Depends(get_reference_contract_service),
) -> PlatformResponse:
    try:
        platform = service.create_platform(name=body.name, requested_by=requested_by)
    except ReferenceContractError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return PlatformResponse.model_validate(platform)


@router.get("/platforms", response_model=list[PlatformResponse])
async def list_platforms(
    service: ReferenceContractService = Depends(get_reference_contract_service),
) -> list[PlatformResponse]:
    return [PlatformResponse.model_validate(p) for p in service.list_platforms()]


@router.post("/contracts", response_model=ContractResponse, status_code=201)
async def create_contract(
    body: CreateContractRequest,
    requested_by: str = Depends(get_current_user_id),
    service: ReferenceContractService = Depends(get_reference_contract_service),
) -> ContractResponse:
    try:
        contract = service.create_contract(
            branch_id=body.branch_id, platform_id=body.platform_id,
            commission_pct=body.commission_pct, valid_from=body.valid_from,
            valid_to=body.valid_to, requested_by=requested_by,
        )
    except ReferenceContractError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return ContractResponse.model_validate(contract)


@router.get("/branches/{branch_id}/contracts", response_model=list[ContractResponse])
async def list_contracts_for_branch(
    branch_id: str,
    service: ReferenceContractService = Depends(get_reference_contract_service),
) -> list[ContractResponse]:
    contracts = service.list_contracts_for_branch(branch_id=branch_id)
    return [ContractResponse.model_validate(c) for c in contracts]
