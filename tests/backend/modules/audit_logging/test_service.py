"""
Unit tests for AuditLogService.

All cross-module imports live at module level, learned from Organization
& Branch Management and Reference & Contract Configuration's own test
suites, so every needed table registers before create_all() runs.

The integration test classes are the real point of this module: the same
AuditLogService instance is handed to Identity & Access, Analysis
Orchestration, and Data Import's services in turn, proving one real
implementation satisfies all of their structurally-identical AuditLogger
protocols at once - not one bespoke implementation per module.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from modules.analysis_orchestration.models import Analysis, AnalysisStatus
from modules.analysis_orchestration.service import AnalysisOrchestrationService
from modules.audit_logging.models import AuditLogEntry
from modules.audit_logging.service import AuditLogService
from modules.identity_access.models import User, UserRole
from modules.identity_access.service import IdentityAccessService
from modules.imports.models import Base
from modules.organizations.models import Branch

USER_ID = "user-1"
BRANCH_ID = "branch-1"


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def service(db):
    return AuditLogService(db=db)


class _AlwaysFailsOnCommit:
    """Not production code - a minimal duck-typed Session stand-in that
    always fails on commit, to exercise the swallow-the-failure path
    without needing a real broken database connection."""

    def add(self, obj) -> None:
        pass

    def commit(self) -> None:
        raise RuntimeError("simulated database failure")

    def rollback(self) -> None:
        pass


class TestLog:
    def test_creates_entry(self, service, db):
        service.log(event="test_event", user_id=USER_ID, analysis_id=None, metadata={"key": "value"})
        entry = db.execute(select(AuditLogEntry)).scalars().first()
        assert entry.event == "test_event"
        assert entry.user_id == USER_ID

    def test_null_analysis_id_allowed(self, service, db):
        service.log(event="user_created", user_id=USER_ID, analysis_id=None, metadata={})
        entry = db.execute(select(AuditLogEntry)).scalars().first()
        assert entry.analysis_id is None

    def test_metadata_round_trips_as_a_real_dict(self, service, db):
        service.log(
            event="test_event", user_id=USER_ID, analysis_id=None,
            metadata={"count": 3, "nested": {"a": 1}},
        )
        entry = db.execute(select(AuditLogEntry)).scalars().first()
        assert entry.details == {"count": 3, "nested": {"a": 1}}
        assert isinstance(entry.details, dict)  # not a JSON string

    def test_persist_failure_is_swallowed_not_raised(self):
        service = AuditLogService(db=_AlwaysFailsOnCommit())
        service.log(event="test_event", user_id=USER_ID, analysis_id=None, metadata={})  # must not raise


class TestListEntries:
    def test_filters_by_analysis_id(self, service):
        service.log(event="e1", user_id=USER_ID, analysis_id="analysis-a", metadata={})
        service.log(event="e2", user_id=USER_ID, analysis_id="analysis-b", metadata={})
        results = service.list_entries(analysis_id="analysis-a")
        assert len(results) == 1
        assert results[0].event == "e1"

    def test_filters_by_user_id(self, service):
        service.log(event="e1", user_id="user-a", analysis_id=None, metadata={})
        service.log(event="e2", user_id="user-b", analysis_id=None, metadata={})
        results = service.list_entries(user_id="user-a")
        assert len(results) == 1

    def test_filters_by_event(self, service):
        service.log(event="login", user_id=USER_ID, analysis_id=None, metadata={})
        service.log(event="logout", user_id=USER_ID, analysis_id=None, metadata={})
        results = service.list_entries(event="login")
        assert len(results) == 1
        assert results[0].event == "login"

    def test_respects_limit(self, service):
        for i in range(5):
            service.log(event=f"e{i}", user_id=USER_ID, analysis_id=None, metadata={})
        assert len(service.list_entries(limit=2)) == 2

    def test_orders_newest_first(self, service):
        service.log(event="first", user_id=USER_ID, analysis_id=None, metadata={})
        service.log(event="second", user_id=USER_ID, analysis_id=None, metadata={})
        results = service.list_entries()
        assert results[0].event == "second"


class TestSatisfiesIdentityAccessAuditLogger:
    def test_real_service_works_with_identity_access(self, db, service):
        identity_service = IdentityAccessService(db=db, audit_logger=service)
        user = identity_service.create_user(
            email="owner@example.com", password="s3cret!", role=UserRole.OWNER
        )
        entries = service.list_entries(event="user_created")
        assert len(entries) == 1
        assert entries[0].user_id == user.id


class TestSatisfiesAnalysisOrchestrationAuditLogger:
    def test_real_service_works_with_analysis_orchestration(self, db, service):
        user = User(email="owner2@example.com", password_hash="x", role=UserRole.OWNER)
        db.add(user)
        db.commit()
        db.refresh(user)

        branch = Branch(organization_id="org-1", name="Downtown", timezone="Asia/Riyadh")
        db.add(branch)
        db.commit()
        db.refresh(branch)

        orchestration_service = AnalysisOrchestrationService(db=db, audit_logger=service)
        analysis = orchestration_service.create_analysis(
            branch_id=branch.id, created_by=user.id,
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        entries = service.list_entries(event="analysis_created")
        assert len(entries) == 1
        assert entries[0].analysis_id == analysis.id


class TestSatisfiesDataImportAuditLogger:
    def test_real_service_works_with_data_import(self, db, tmp_path, service):
        from cryptography.fernet import Fernet

        from modules.imports.dependencies import AnalysisStatus as ImportAnalysisStatus
        from modules.imports.dependencies import AuthContext, InMemoryAnalysisLookup
        from modules.imports.models import SourceType
        from modules.imports.security import NoOpMalwareScanner
        from modules.imports.service import ImportService
        from storage.file_storage import LocalEncryptedFileStorage

        analysis_lookup = InMemoryAnalysisLookup()
        analysis_lookup.register("analysis-x", ImportAnalysisStatus.AWAITING_FILES, BRANCH_ID)

        storage = LocalEncryptedFileStorage(tmp_path / "files", Fernet.generate_key())
        import_service = ImportService(
            db=db, storage=storage, analysis_lookup=analysis_lookup,
            audit_logger=service, scanner=NoOpMalwareScanner(),
        )
        auth = AuthContext(user_id=USER_ID, accessible_branch_ids=frozenset({BRANCH_ID}))
        import_service.import_file(
            analysis_id="analysis-x", source_type=SourceType.POS_EXPORT,
            original_filename="pos.csv", content=b"order_id,amount\n1,10.00\n", auth=auth,
        )
        entries = service.list_entries(event="file_import_succeeded")
        assert len(entries) == 1
