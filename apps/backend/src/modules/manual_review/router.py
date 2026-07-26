"""
HTTP layer for the Manual Review & Override module.

Deliberately thin, same pattern as every other module's router. Three
independent endpoints for three independent actions - no shared prefix,
since these attach to different resources (a match, a discrepancy, an
analysis).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import ManualReviewError_
from .schemas import (
    DiscrepancyReviewRequest,
    DiscrepancyReviewResponse,
    ManualMatchRequest,
    ManualMatchResponse,
    MatchReviewRequest,
    MatchReviewResponse,
)
from .service import ManualReviewService

router = APIRouter(tags=["manual-review"])


def get_manual_review_service() -> ManualReviewService:
    """Real wiring assembled at application start-up, same pattern as the
    other modules' routers."""
    raise NotImplementedError(
        "Wire up ManualReviewService (db session, audit_logger) at "
        "application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post(
    "/matches/{reconciliation_match_id}/review",
    response_model=MatchReviewResponse,
    status_code=201,
)
async def review_match(
    reconciliation_match_id: str,
    body: MatchReviewRequest,
    reviewed_by: str = Depends(get_current_user_id),
    service: ManualReviewService = Depends(get_manual_review_service),
) -> MatchReviewResponse:
    try:
        record = service.review_match(
            reconciliation_match_id=reconciliation_match_id,
            decision=body.decision,
            reviewed_by=reviewed_by,
            note=body.note,
        )
    except ManualReviewError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return MatchReviewResponse.model_validate(record)


@router.post(
    "/discrepancies/{discrepancy_id}/review",
    response_model=DiscrepancyReviewResponse,
    status_code=201,
)
async def review_discrepancy(
    discrepancy_id: str,
    body: DiscrepancyReviewRequest,
    reviewed_by: str = Depends(get_current_user_id),
    service: ManualReviewService = Depends(get_manual_review_service),
) -> DiscrepancyReviewResponse:
    try:
        record = service.review_discrepancy(
            discrepancy_id=discrepancy_id,
            decision=body.decision,
            reviewed_by=reviewed_by,
            note=body.note,
        )
    except ManualReviewError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return DiscrepancyReviewResponse.model_validate(record)


@router.post(
    "/analyses/{analysis_id}/manual-match",
    response_model=ManualMatchResponse,
    status_code=201,
)
async def create_manual_match(
    analysis_id: str,
    body: ManualMatchRequest,
    reviewed_by: str = Depends(get_current_user_id),
    service: ManualReviewService = Depends(get_manual_review_service),
) -> ManualMatchResponse:
    try:
        match, review = service.create_manual_match(
            analysis_id=analysis_id,
            pos_transaction_id=body.pos_transaction_id,
            platform_transaction_id=body.platform_transaction_id,
            reviewed_by=reviewed_by,
            note=body.note,
        )
    except ManualReviewError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc

    return ManualMatchResponse(
        reconciliation_match_id=match.id,
        pos_transaction_id=match.pos_transaction_id,
        platform_transaction_id=match.platform_transaction_id,
        review=MatchReviewResponse.model_validate(review),
    )
