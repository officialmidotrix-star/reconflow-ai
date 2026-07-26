"""
HTTP layer for the Notification module.

Deliberately thin, same pattern as every other module's router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .exceptions import NotificationError_
from .schemas import NotificationResponse, SendNotificationRequest
from .service import NotificationService

router = APIRouter(prefix="/analyses/{analysis_id}/notifications", tags=["notification"])


def get_notification_service() -> NotificationService:
    """Real wiring assembled at application start-up - this is where the
    choice of channel (SMTPEmailChannel today, others later) gets made, as
    a configuration decision, not a code change."""
    raise NotImplementedError(
        "Wire up NotificationService (db session, channel, audit_logger) "
        "at application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("", response_model=NotificationResponse, status_code=201)
async def send_notification(
    analysis_id: str,
    body: SendNotificationRequest,
    requested_by: str = Depends(get_current_user_id),
    service: NotificationService = Depends(get_notification_service),
) -> NotificationResponse:
    try:
        notification = service.send_notification(
            analysis_id=analysis_id,
            event_type=body.event_type,
            recipient=body.recipient,
            requested_by=requested_by,
        )
    except NotificationError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return NotificationResponse.model_validate(notification)
