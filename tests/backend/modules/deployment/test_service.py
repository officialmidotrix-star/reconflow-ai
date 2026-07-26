"""
Unit tests for DeploymentService. Cross-module imports live at module
level, same lesson learned from every prior foundational module's test
suite, so needed tables register before create_all() runs.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from modules.audit_logging.service import AuditLogService
from modules.analysis_orchestration.models import Analysis  # noqa: F401 - registers analyses
from modules.organizations.models import Branch  # noqa: F401 - registers branches
from modules.deployment.dependencies import InMemoryAuditLogger
from modules.deployment.exceptions import InsufficientPrivilegeError
from modules.deployment.models import DEFAULT_VERSION, DeploymentInfo, LicenseStatus, UpdateEvent
from modules.deployment.service import DeploymentService
from modules.identity_access.models import User, UserRole
from modules.imports.models import Base

TODAY = date(2026, 6, 15)


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def audit_logger():
    return InMemoryAuditLogger()


@pytest.fixture()
def service(db, audit_logger):
    return DeploymentService(db=db, audit_logger=audit_logger)


def _make_user(db, *, role: UserRole = UserRole.OWNER, email: str = "owner@example.com") -> User:
    user = User(email=email, password_hash="x", role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class TestGetDeploymentInfo:
    def test_auto_creates_singleton_on_first_call(self, service):
        info = service.get_deployment_info()
        assert info.current_version == DEFAULT_VERSION

    def test_returns_same_singleton_on_subsequent_calls(self, service, db):
        first = service.get_deployment_info()
        second = service.get_deployment_info()
        assert first.id == second.id
        assert len(db.execute(select(DeploymentInfo)).scalars().all()) == 1


class TestLicenseStatus:
    def test_unlicensed_when_no_key(self, service):
        assert service.get_license_status(today=TODAY) == LicenseStatus.UNLICENSED

    def test_active_with_no_expiry(self, service, db):
        owner = _make_user(db)
        service.record_license(license_key="ABC-123", expires_at=None, requested_by=owner.id)
        assert service.get_license_status(today=TODAY) == LicenseStatus.ACTIVE

    def test_active_when_expiry_in_future(self, service, db):
        owner = _make_user(db)
        service.record_license(
            license_key="ABC-123", expires_at=TODAY + timedelta(days=30), requested_by=owner.id
        )
        assert service.get_license_status(today=TODAY) == LicenseStatus.ACTIVE

    def test_expired_when_expiry_in_past(self, service, db):
        owner = _make_user(db)
        service.record_license(
            license_key="ABC-123", expires_at=TODAY - timedelta(days=1), requested_by=owner.id
        )
        assert service.get_license_status(today=TODAY) == LicenseStatus.EXPIRED


class TestRecordLicense:
    def test_owner_succeeds(self, service, db):
        owner = _make_user(db)
        info = service.record_license(license_key="ABC-123", expires_at=None, requested_by=owner.id)
        assert info.license_key == "ABC-123"

    def test_non_owner_rejected(self, service, db):
        accountant = _make_user(db, role=UserRole.ACCOUNTANT, email="acct@example.com")
        with pytest.raises(InsufficientPrivilegeError):
            service.record_license(license_key="ABC-123", expires_at=None, requested_by=accountant.id)

    def test_unknown_user_rejected(self, service):
        with pytest.raises(InsufficientPrivilegeError):
            service.record_license(license_key="ABC-123", expires_at=None, requested_by="does-not-exist")


class TestRecordUpdate:
    def test_advances_current_version(self, service, db):
        owner = _make_user(db)
        service.record_update(version="0.2.0", notes="Added reporting module", requested_by=owner.id)
        assert service.get_deployment_info().current_version == "0.2.0"

    def test_creates_history_entry(self, service, db):
        owner = _make_user(db)
        event = service.record_update(version="0.2.0", notes="notes here", requested_by=owner.id)
        assert event.version == "0.2.0"
        assert event.notes == "notes here"

    def test_non_owner_rejected(self, service, db):
        ops = _make_user(db, role=UserRole.OPS_MANAGER, email="ops@example.com")
        with pytest.raises(InsufficientPrivilegeError):
            service.record_update(version="0.2.0", notes=None, requested_by=ops.id)


class TestUpdateHistory:
    def test_ordered_newest_first(self, service, db):
        owner = _make_user(db)
        service.record_update(version="0.2.0", notes=None, requested_by=owner.id)
        service.record_update(version="0.3.0", notes=None, requested_by=owner.id)
        history = service.list_update_history()
        assert [e.version for e in history] == ["0.3.0", "0.2.0"]


class TestAuditLogging:
    def test_records_license_and_update_events(self, service, db, audit_logger):
        owner = _make_user(db)
        service.record_license(license_key="ABC-123", expires_at=None, requested_by=owner.id)
        assert audit_logger.records[-1].event == "license_recorded"
        service.record_update(version="0.2.0", notes=None, requested_by=owner.id)
        assert audit_logger.records[-1].event == "update_recorded"


class TestSatisfiesAuditLoggerWithRealService:
    def test_real_audit_log_service_works_as_dependency(self, db):
        real_audit_logger = AuditLogService(db=db)
        deployment_service = DeploymentService(db=db, audit_logger=real_audit_logger)
        owner = _make_user(db)

        deployment_service.record_update(version="0.2.0", notes=None, requested_by=owner.id)

        entries = real_audit_logger.list_entries(event="update_recorded")
        assert len(entries) == 1
        assert entries[0].user_id == owner.id
