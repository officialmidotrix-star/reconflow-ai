"""
Unit tests for AnalysisOrchestrationService.

The last test class is the interesting one: it constructs a real
ImportService using this module's real AnalysisOrchestrationService as
its AnalysisLookup dependency - proving the protocol Data Import declared
all the way back at the start of this build is now genuinely satisfied by
a real implementation, not just structurally compatible in theory.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import Column, String, Table, create_engine
from sqlalchemy.orm import Session

from modules.analysis_orchestration.dependencies import InMemoryAuditLogger
from modules.analysis_orchestration.exceptions import AnalysisNotFoundError, InvalidStatusTransitionError
from modules.analysis_orchestration.models import Analysis, AnalysisStatus
from modules.analysis_orchestration.service import AnalysisOrchestrationService
from modules.imports.models import Base

USER_ID = "user-1"
BRANCH_ID = "branch-1"

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
def audit_logger():
    return InMemoryAuditLogger()


@pytest.fixture()
def service(db, audit_logger):
    return AnalysisOrchestrationService(db=db, audit_logger=audit_logger)


def _create(service, **overrides):
    defaults = dict(
        branch_id=BRANCH_ID,
        created_by=USER_ID,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
    )
    defaults.update(overrides)
    return service.create_analysis(**defaults)


class TestCreateAnalysis:
    def test_starts_awaiting_files(self, service):
        analysis = _create(service)
        assert analysis.status == AnalysisStatus.AWAITING_FILES
        assert analysis.version == 1
        assert analysis.parent_analysis_id is None


class TestMarkProcessing:
    def test_from_awaiting_files_succeeds(self, service):
        analysis = _create(service)
        updated = service.mark_processing(analysis_id=analysis.id, requested_by=USER_ID)
        assert updated.status == AnalysisStatus.PROCESSING

    def test_from_completed_rejected(self, service):
        analysis = _create(service)
        service.mark_processing(analysis_id=analysis.id, requested_by=USER_ID)
        service.mark_completed(analysis_id=analysis.id, requested_by=USER_ID)
        with pytest.raises(InvalidStatusTransitionError):
            service.mark_processing(analysis_id=analysis.id, requested_by=USER_ID)


class TestMarkCompleted:
    def test_from_processing_succeeds(self, service):
        analysis = _create(service)
        service.mark_processing(analysis_id=analysis.id, requested_by=USER_ID)
        updated = service.mark_completed(analysis_id=analysis.id, requested_by=USER_ID)
        assert updated.status == AnalysisStatus.COMPLETED

    def test_from_awaiting_files_rejected(self, service):
        analysis = _create(service)
        with pytest.raises(InvalidStatusTransitionError):
            service.mark_completed(analysis_id=analysis.id, requested_by=USER_ID)


class TestMarkFailed:
    def test_from_processing_records_reason(self, service):
        analysis = _create(service)
        service.mark_processing(analysis_id=analysis.id, requested_by=USER_ID)
        updated = service.mark_failed(
            analysis_id=analysis.id, reason="Normalization step timed out.", requested_by=USER_ID
        )
        assert updated.status == AnalysisStatus.FAILED
        assert updated.failure_reason == "Normalization step timed out."

    def test_from_awaiting_files_rejected(self, service):
        analysis = _create(service)
        with pytest.raises(InvalidStatusTransitionError):
            service.mark_failed(analysis_id=analysis.id, reason="x", requested_by=USER_ID)


class TestCreateNewVersion:
    def test_from_completed_increments_version_and_links_parent(self, service):
        analysis = _create(service)
        service.mark_processing(analysis_id=analysis.id, requested_by=USER_ID)
        service.mark_completed(analysis_id=analysis.id, requested_by=USER_ID)

        new_version = service.create_new_version(previous_analysis_id=analysis.id, requested_by=USER_ID)
        assert new_version.version == 2
        assert new_version.parent_analysis_id == analysis.id
        assert new_version.status == AnalysisStatus.AWAITING_FILES

    def test_from_failed_succeeds(self, service):
        analysis = _create(service)
        service.mark_processing(analysis_id=analysis.id, requested_by=USER_ID)
        service.mark_failed(analysis_id=analysis.id, reason="x", requested_by=USER_ID)

        new_version = service.create_new_version(previous_analysis_id=analysis.id, requested_by=USER_ID)
        assert new_version.version == 2

    def test_from_processing_rejected(self, service):
        analysis = _create(service)
        service.mark_processing(analysis_id=analysis.id, requested_by=USER_ID)
        with pytest.raises(InvalidStatusTransitionError):
            service.create_new_version(previous_analysis_id=analysis.id, requested_by=USER_ID)


class TestNotFound:
    def test_get_analysis_not_found(self, service):
        with pytest.raises(AnalysisNotFoundError):
            service.get_analysis("does-not-exist")

    def test_mark_processing_not_found(self, service):
        with pytest.raises(AnalysisNotFoundError):
            service.mark_processing(analysis_id="does-not-exist", requested_by=USER_ID)


class TestSatisfiesDataImportAnalysisLookup:
    def test_real_orchestration_service_works_as_import_services_analysis_lookup(
        self, db, audit_logger, tmp_path, service
    ):
        from cryptography.fernet import Fernet

        from modules.imports.dependencies import AuthContext
        from modules.imports.dependencies import InMemoryAuditLogger as ImportAuditLogger
        from modules.imports.models import SourceType
        from modules.imports.security import NoOpMalwareScanner
        from modules.imports.service import ImportService
        from modules.imports.storage import LocalEncryptedFileStorage

        analysis = _create(service)

        storage = LocalEncryptedFileStorage(tmp_path / "files", Fernet.generate_key())
        import_service = ImportService(
            db=db,
            storage=storage,
            analysis_lookup=service,  # the real AnalysisOrchestrationService, not a stub
            audit_logger=ImportAuditLogger(),
            scanner=NoOpMalwareScanner(),
        )
        auth = AuthContext(user_id=USER_ID, accessible_branch_ids=frozenset({BRANCH_ID}))

        record = import_service.import_file(
            analysis_id=analysis.id,
            source_type=SourceType.POS_EXPORT,
            original_filename="pos.csv",
            content=b"order_id,amount\n1,10.00\n",
            auth=auth,
        )
        assert record.analysis_id == analysis.id


class TestAuditLogging:
    def test_create_logs(self, service, audit_logger):
        _create(service)
        assert audit_logger.records[-1].event == "analysis_created"

    def test_transition_logs(self, service, audit_logger):
        analysis = _create(service)
        service.mark_processing(analysis_id=analysis.id, requested_by=USER_ID)
        assert audit_logger.records[-1].event == "analysis_marked_processing"
        assert audit_logger.records[-1].analysis_id == analysis.id
