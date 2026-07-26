"""
HTTP layer for the AI Insights module.

Deliberately thin, same pattern as every other module's router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import AIInsightError_
from .schemas import AIInsightResponse
from .service import AIInsightService

router = APIRouter(prefix="/analyses/{analysis_id}/ai-insights", tags=["ai-insights"])


def get_ai_insight_service() -> AIInsightService:
    """Real wiring assembled at application start-up - this is where the
    choice between AnthropicAIProvider and a future self-hosted provider
    gets made, as a configuration decision, not a code change."""
    raise NotImplementedError(
        "Wire up AIInsightService (db session, ai_provider, audit_logger) "
        "at application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("", response_model=AIInsightResponse, status_code=201)
async def generate_ai_insight(
    analysis_id: str,
    requested_by: str = Depends(get_current_user_id),
    service: AIInsightService = Depends(get_ai_insight_service),
) -> AIInsightResponse:
    try:
        record = service.generate_insight(analysis_id=analysis_id, requested_by=requested_by)
    except AIInsightError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc

    return AIInsightResponse.model_validate(record)
