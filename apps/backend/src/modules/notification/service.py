"""
Core business logic for the Notification module.

`send_notification` always returns a Notification record - SENT or
FAILED. A channel exception is caught and recorded as FAILED, not
re-raised: the pipeline shouldn't halt because a send attempt failed, but
that failure still needs to be visible and permanent, same as every other
outcome this module records.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from modules.analysis_orchestration.models import Analysis

from .dependencies import AuditLogger, NotificationChannel
from .exceptions import AnalysisNotFoundError, NotificationPersistError
from .models import Notification, NotificationEventType, NotificationStatus


class NotificationService:
    def __init__(self, *, db: Session, channel: NotificationChannel, audit_logger: AuditLogger) -> None:
        self._db = db
        self._channel = channel
        self._audit_logger = audit_logger

    def send_notification(
        self,
        *,
        analysis_id: str,
        event_type: NotificationEventType,
        recipient: str,
        requested_by: str,
    ) -> Notification:
        analysis = self._db.get(Analysis, analysis_id)
        if analysis is None:
            raise AnalysisNotFoundError("We couldn't find that analysis.")

        subject, body = self._compose_message(analysis, event_type)

        status = NotificationStatus.SENT
        failure_reason: str | None = None
        try:
            self._channel.send(recipient=recipient, subject=subject, body=body)
        except Exception as exc:  # noqa: BLE001
            status = NotificationStatus.FAILED
            failure_reason = str(exc)

        notification = Notification(
            analysis_id=analysis_id,
            event_type=event_type,
            channel=self._channel.channel_name,
            recipient=recipient,
            status=status,
            failure_reason=failure_reason,
        )
        self._db.add(notification)
        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise NotificationPersistError(
                "We couldn't save the notification record. Please try again."
            ) from exc
        self._db.refresh(notification)

        self._audit_logger.log(
            event="notification_attempted",
            user_id=requested_by,
            analysis_id=analysis_id,
            metadata={
                "event_type": event_type.value,
                "channel": self._channel.channel_name,
                "status": status.value,
            },
        )
        return notification

    # -- internal steps -------------------------------------------------

    def _compose_message(
        self, analysis: Analysis, event_type: NotificationEventType
    ) -> tuple[str, str]:
        period = f"{analysis.period_start} to {analysis.period_end}"
        if event_type == NotificationEventType.ANALYSIS_COMPLETED:
            subject = f"Your reconciliation analysis for {period} is ready"
            body = (
                f"Analysis {analysis.id} (version {analysis.version}) for {period} "
                "has completed successfully. Sign in to review the results."
            )
        else:
            subject = f"Your reconciliation analysis for {period} needs attention"
            reason = analysis.failure_reason or "An unexpected error occurred."
            body = (
                f"Analysis {analysis.id} (version {analysis.version}) for {period} "
                f"could not be completed: {reason}"
            )
        return subject, body
