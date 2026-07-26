from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .models import NotificationEventType, NotificationStatus


class SendNotificationRequest(BaseModel):
    event_type: NotificationEventType
    recipient: str


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_id: str
    event_type: NotificationEventType
    channel: str
    recipient: str
    status: NotificationStatus
    failure_reason: str | None
    sent_at: datetime
