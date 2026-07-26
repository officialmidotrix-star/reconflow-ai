"""
HTTP layer for the Analysis Orchestration module.

Includes a GET endpoint, breaking from every prior module's "defer GET
until a real consumer needs it" pattern - this module's whole purpose is
exposing current status, which both n8n (deciding whether to proceed) and
a future processing screen (polling for completion) need immediately, not
speculatively.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import AnalysisOrchestrationError_
from .schemas import AnalysisResponse, CreateAnalysisRequest, MarkFailedRequest
from .service import AnalysisOrchestrationService

router = APIRouter(prefix="/analyses", tags=["analysis-orchestration"])


def get_orchestration_service() -> AnalysisOrchestrationService:
    """Real wiring assembled at application start-up, same pattern as
    every other module's router. This is also the instance that should be
    passed wherever modules.imports.dependencies.AnalysisLookup is
    expected in production - it satisfies that protocol directly."""
    raise NotImplementedError(
        "Wire up AnalysisOrchestrationService (db session, audit_logger) "
        "at application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("", response_model=AnalysisResponse, status_code=201)
async def create_analysis(
    body: CreateAnalysisRequest,
    created_by: str = Depends(get_current_user_id),
    service: AnalysisOrchestrationService = Depends(get_orchestration_service),
) -> AnalysisResponse:
    analysis = service.create_analysis(
        branch_id=body.branch_id,
        created_by=created_by,
        period_start=body.period_start,
        period_end=body.period_end,
    )
    return AnalysisResponse.model_validate(analysis)


@router.get("/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(
    analysis_id: str,
    service: AnalysisOrchestrationService = Depends(get_orchestration_service),
) -> AnalysisResponse:
    try:
        analysis = service.get_analysis(analysis_id)
    except AnalysisOrchestrationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return AnalysisResponse.model_validate(analysis)


@router.post("/{analysis_id}/versions", response_model=AnalysisResponse, status_code=201)
async def create_new_version(
    analysis_id: str,
    requested_by: str = Depends(get_current_user_id),
    service: AnalysisOrchestrationService = Depends(get_orchestration_service),
) -> AnalysisResponse:
    try:
        analysis = service.create_new_version(previous_analysis_id=analysis_id, requested_by=requested_by)
    except AnalysisOrchestrationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return AnalysisResponse.model_validate(analysis)


@router.post("/{analysis_id}/mark-processing", response_model=AnalysisResponse)
async def mark_processing(
    analysis_id: str,
    requested_by: str = Depends(get_current_user_id),
    service: AnalysisOrchestrationService = Depends(get_orchestration_service),
) -> AnalysisResponse:
    try:
        analysis = service.mark_processing(analysis_id=analysis_id, requested_by=requested_by)
    except AnalysisOrchestrationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return AnalysisResponse.model_validate(analysis)


@router.post("/{analysis_id}/mark-completed", response_model=AnalysisResponse)
async def mark_completed(
    analysis_id: str,
    requested_by: str = Depends(get_current_user_id),
    service: AnalysisOrchestrationService = Depends(get_orchestration_service),
) -> AnalysisResponse:
    try:
        analysis = service.mark_completed(analysis_id=analysis_id, requested_by=requested_by)
    except AnalysisOrchestrationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return AnalysisResponse.model_validate(analysis)


@router.post("/{analysis_id}/mark-failed", response_model=AnalysisResponse)
async def mark_failed(
    analysis_id: str,
    body: MarkFailedRequest,
    requested_by: str = Depends(get_current_user_id),
    service: AnalysisOrchestrationService = Depends(get_orchestration_service),
) -> AnalysisResponse:
    try:
        analysis = service.mark_failed(
            analysis_id=analysis_id, reason=body.reason, requested_by=requested_by
        )
    except AnalysisOrchestrationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return AnalysisResponse.model_validate(analysis)
