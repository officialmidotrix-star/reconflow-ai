"""
Unit tests for NotificationService. Uses FakeNotificationChannel
exclusively - a unit test suite should never depend on a live SMTP server.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import Column, String, Table, create_engine, select
from sqlalchemy.orm import Session

from modules.analysis_orchestration.models import Analysis, AnalysisStatus
from modules.imports.models import Base
from modules.notification.dependencies import FakeNotificationChannel, InMemoryAuditLogger
from modules.notification.exceptions import AnalysisNotFoundError
from modules.notification.models import Notification, NotificationEventType, NotificationStatus
from modules.notification.service import NotificationService

BRANCH_ID = "branch-1"
USER_ID = "user-1"

# "branches" is now a real table (Organization & Branch Management) -
# importing its model registers it, replacing the stand-in this file
# used before that module existed.
from modules.organizations.models import Branch  # noqa: E402,F401
# "users" is now a real table (Identity & Access) - importing its model
# registers it, replacing the stand-in this file used before that module
# existed.
from modules.identity_access.models import User  # noqa: E402,F401


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def channel():
    return FakeNotificationChannel()


@pytest.fixture()
def audit_logger():
    return InMemoryAuditLogger()


@pytest.fixture()
def service(db, channel, audit_logger):
    return NotificationService(db=db, channel=channel, audit_logger=audit_logger)


def _make_analysis(
    db: Session, *, status: AnalysisStatus = AnalysisStatus.COMPLETED, failure_reason: str | None = None
) -> Analysis:
    analysis = Analysis(
        branch_id=BRANCH_ID,
        created_by=USER_ID,
        version=1,
        status=status,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        failure_reason=failure_reason,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


class TestSuccessfulNotification:
    def test_completed_notification_sent_and_recorded(self, service, db, channel):
        analysis = _make_analysis(db)
        notification = service.send_notification(
            analysis_id=analysis.id, event_type=NotificationEventType.ANALYSIS_COMPLETED,
            recipient="owner@example.com", requested_by=USER_ID,
        )
        assert notification.status == NotificationStatus.SENT
        assert notification.channel == "fake"
        assert len(channel.sent) == 1
        recipient, subject, body = channel.sent[0]
        assert recipient == "owner@example.com"
        assert "ready" in subject.lower()

    def test_failed_analysis_notification_includes_reason(self, service, db, channel):
        analysis = _make_analysis(
            db, status=AnalysisStatus.FAILED, failure_reason="Normalization step timed out."
        )
        service.send_notification(
            analysis_id=analysis.id, event_type=NotificationEventType.ANALYSIS_FAILED,
            recipient="owner@example.com", requested_by=USER_ID,
        )
        _, _, body = channel.sent[0]
        assert "Normalization step timed out." in body


class TestChannelFailure:
    def test_recorded_as_failed_not_raised(self, db, audit_logger):
        failing_channel = FakeNotificationChannel(should_fail=True)
        service = NotificationService(db=db, channel=failing_channel, audit_logger=audit_logger)
        analysis = _make_analysis(db)

        notification = service.send_notification(
            analysis_id=analysis.id, event_type=NotificationEventType.ANALYSIS_COMPLETED,
            recipient="owner@example.com", requested_by=USER_ID,
        )
        assert notification.status == NotificationStatus.FAILED
        assert "simulated channel failure" in notification.failure_reason


class TestNotFound:
    def test_analysis_not_found_raises(self, service):
        with pytest.raises(AnalysisNotFoundError):
            service.send_notification(
                analysis_id="does-not-exist", event_type=NotificationEventType.ANALYSIS_COMPLETED,
                recipient="owner@example.com", requested_by=USER_ID,
            )


class TestHistoryPreserved:
    def test_multiple_notifications_all_persist(self, service, db):
        analysis = _make_analysis(db)
        service.send_notification(
            analysis_id=analysis.id, event_type=NotificationEventType.ANALYSIS_COMPLETED,
            recipient="owner@example.com", requested_by=USER_ID,
        )
        service.send_notification(
            analysis_id=analysis.id, event_type=NotificationEventType.ANALYSIS_COMPLETED,
            recipient="accountant@example.com", requested_by=USER_ID,
        )
        notifications = db.execute(
            select(Notification).where(Notification.analysis_id == analysis.id)
        ).scalars().all()
        assert len(notifications) == 2  # neither superseded


class TestAuditLogging:
    def test_records_status_in_metadata(self, service, db, audit_logger):
        analysis = _make_analysis(db)
        service.send_notification(
            analysis_id=analysis.id, event_type=NotificationEventType.ANALYSIS_COMPLETED,
            recipient="owner@example.com", requested_by=USER_ID,
        )
        assert audit_logger.records[-1].event == "notification_attempted"
        assert audit_logger.records[-1].metadata["status"] == "SENT"
