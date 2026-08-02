"""
HTTP layer for the Analysis Results module.

Shares the "/analyses" prefix with Analysis Orchestration's router - a
second APIRouter under the same prefix, not an extension of that
module's own router. Kept separate because these two endpoints are a
different concern (a cross-module read model for a frontend) from that
module's actual job (owning the analysis lifecycle state machine), same
reasoning every other module boundary in this codebase already follows.
FastAPI has no issue mounting two routers under one shared prefix as
long as the full paths don't collide, and they don't:
/analyses/{id}/mark-processing (Orchestration) vs. /analyses/{id}/summary
(this module).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import AnalysisResultsError_
from .schemas import AnalysisSummaryResponse, DiscrepancyDetailResponse
from .service import AnalysisResultsService

router = APIRouter(prefix="/analyses", tags=["analysis-results"])


def get_results_service() -> AnalysisResultsService:
    """Real wiring assembled at application start-up, same pattern as
    every other module's router."""
    raise NotImplementedError(
        "Wire up AnalysisResultsService (db session) at application start-up "
        "and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.get("/{analysis_id}/summary", response_model=AnalysisSummaryResponse)
async def get_summary(
    analysis_id: str,
    _requested_by: str = Depends(get_current_user_id),
    service: AnalysisResultsService = Depends(get_results_service),
) -> AnalysisSummaryResponse:
    try:
        return service.get_summary(analysis_id)
    except AnalysisResultsError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc


@router.get("/{analysis_id}/discrepancies", response_model=list[DiscrepancyDetailResponse])
async def list_discrepancies(
    analysis_id: str,
    _requested_by: str = Depends(get_current_user_id),
    service: AnalysisResultsService = Depends(get_results_service),
) -> list[DiscrepancyDetailResponse]:
    try:
        return service.list_discrepancies(analysis_id)
    except AnalysisResultsError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
