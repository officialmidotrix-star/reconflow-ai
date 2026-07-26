"""
Unit tests for OrganizationService.

The last test class mirrors Analysis Orchestration's and Identity &
Access's own integration tests: it builds this module's real service and
hands it straight to a real NormalizationService.normalize_file() call,
proving the AnalysisTimezoneLookup shape Normalization declared as a
stand-in is now satisfied by a real implementation.
"""

from __future__ import annotations

from datetime import date

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, String, Table, create_engine
from sqlalchemy.orm import Session

from modules.analysis_orchestration.models import Analysis, AnalysisStatus
from modules.imports.models import Base, FileStatus, SourceType, UploadedFile
from modules.imports.security import compute_checksum
from modules.normalization.dependencies import InMemoryAuditLogger as NormAuditLogger
from modules.normalization.service import NormalizationService
from modules.organizations.dependencies import InMemoryAuditLogger
from modules.organizations.exceptions import (
    BranchNotFoundError,
    InvalidTimezoneError,
    OrganizationAlreadyExistsError,
    OrganizationNotFoundError,
)
from modules.organizations.service import OrganizationService
from modules.validation.dependencies import InMemoryAuditLogger as ValidationAuditLogger
from modules.validation.service import ValidationService
from storage.file_storage import LocalEncryptedFileStorage

USER_ID = "user-1"

if "users" not in Base.metadata.tables:
    Table("users", Base.metadata, Column("id", String(36), primary_key=True))


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
    return OrganizationService(db=db, audit_logger=audit_logger)


def _make_org(service, legal_name="Acme Restaurants", default_currency="sar"):
    return service.create_organization(
        legal_name=legal_name, default_currency=default_currency, requested_by=USER_ID
    )


class TestCreateOrganization:
    def test_succeeds(self, service):
        org = _make_org(service)
        assert org.legal_name == "Acme Restaurants"
        assert org.default_currency == "SAR"  # normalized to uppercase

    def test_second_organization_rejected(self, service):
        _make_org(service)
        with pytest.raises(OrganizationAlreadyExistsError):
            _make_org(service, legal_name="Someone Else")


class TestGetCurrentOrganization:
    def test_returns_none_when_empty(self, service):
        assert service.get_current_organization() is None

    def test_returns_the_org_once_created(self, service):
        org = _make_org(service)
        assert service.get_current_organization().id == org.id


class TestCreateBranch:
    def test_succeeds_under_valid_organization(self, service):
        org = _make_org(service)
        branch = service.create_branch(
            organization_id=org.id, name="Downtown", timezone="Asia/Riyadh", requested_by=USER_ID
        )
        assert branch.timezone == "Asia/Riyadh"
        assert branch.organization_id == org.id

    def test_invalid_timezone_rejected(self, service):
        org = _make_org(service)
        with pytest.raises(InvalidTimezoneError):
            service.create_branch(
                organization_id=org.id, name="Downtown", timezone="Not/A_Real_Zone",
                requested_by=USER_ID,
            )

    def test_unknown_organization_rejected(self, service):
        with pytest.raises(OrganizationNotFoundError):
            service.create_branch(
                organization_id="does-not-exist", name="Downtown", timezone="Asia/Riyadh",
                requested_by=USER_ID,
            )


class TestGetAndListBranches:
    def test_get_branch_not_found_raises(self, service):
        with pytest.raises(BranchNotFoundError):
            service.get_branch(branch_id="does-not-exist")

    def test_list_branches_for_organization(self, service):
        org = _make_org(service)
        service.create_branch(
            organization_id=org.id, name="Downtown", timezone="Asia/Riyadh", requested_by=USER_ID
        )
        service.create_branch(
            organization_id=org.id, name="Uptown", timezone="Asia/Riyadh", requested_by=USER_ID
        )
        branches = service.list_branches(organization_id=org.id)
        assert {b.name for b in branches} == {"Downtown", "Uptown"}


class TestTimezoneAndCurrencyLookup:
    def test_resolves_through_a_real_analysis(self, service, db):
        org = _make_org(service, default_currency="sar")
        branch = service.create_branch(
            organization_id=org.id, name="Downtown", timezone="Asia/Riyadh", requested_by=USER_ID
        )
        analysis = Analysis(
            branch_id=branch.id, created_by=USER_ID, version=1, status=AnalysisStatus.AWAITING_FILES,
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        assert service.get_timezone(analysis.id) == "Asia/Riyadh"
        assert service.get_currency(analysis.id) == "SAR"

    def test_get_timezone_returns_none_for_unknown_analysis(self, service):
        assert service.get_timezone("does-not-exist") is None

    def test_get_currency_returns_none_for_unknown_analysis(self, service):
        assert service.get_currency("does-not-exist") is None


class TestAuditLogging:
    def test_records_organization_and_branch_creation(self, service, audit_logger):
        org = _make_org(service)
        assert audit_logger.records[-1].event == "organization_created"
        service.create_branch(
            organization_id=org.id, name="Downtown", timezone="Asia/Riyadh", requested_by=USER_ID
        )
        assert audit_logger.records[-1].event == "branch_created"


class TestSatisfiesNormalizationTimezoneLookup:
    def test_real_service_works_with_a_real_normalization_call(self, db, tmp_path, service):
        org = _make_org(service, default_currency="sar")
        branch = service.create_branch(
            organization_id=org.id, name="Downtown", timezone="Asia/Riyadh", requested_by=USER_ID
        )
        analysis = Analysis(
            branch_id=branch.id, created_by=USER_ID, version=1, status=AnalysisStatus.AWAITING_FILES,
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        storage = LocalEncryptedFileStorage(tmp_path / "files", Fernet.generate_key())
        content = b"order_id,order_time,amount\n1001,2026-01-15 10:00:00,42.50\n"
        storage_path = f"{branch.id}/{analysis.id}/POS_EXPORT/v1.csv"
        storage.save(storage_path, content)
        uploaded = UploadedFile(
            analysis_id=analysis.id, source_type=SourceType.POS_EXPORT,
            original_filename="pos.csv", storage_path=storage_path,
            checksum_sha256=compute_checksum(content), size_bytes=len(content),
            status=FileStatus.RECEIVED, version=1, uploaded_by=USER_ID,
        )
        db.add(uploaded)
        db.commit()
        db.refresh(uploaded)

        validation_service = ValidationService(
            db=db, storage=storage, audit_logger=ValidationAuditLogger()
        )
        validation_service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)

        normalization_service = NormalizationService(
            db=db, storage=storage, tz_lookup=service, audit_logger=NormAuditLogger(),
        )
        run = normalization_service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert run.rows_created == 1
