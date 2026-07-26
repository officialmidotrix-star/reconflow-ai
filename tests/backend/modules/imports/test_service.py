"""
Unit tests for ImportService.

Uses an in-memory SQLite database (real SQLAlchemy, no mocking of the ORM
layer) plus a temp-directory-backed LocalEncryptedFileStorage (real
encryption, real file I/O) so these tests exercise genuine behavior, not
just mocks calling mocks. The only stand-ins are for modules that don't
exist yet (Analysis Orchestration, Audit Logging, Identity & Access) - see
dependencies.py for why that's the correct boundary, not a shortcut.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, String, Table, create_engine
from sqlalchemy.orm import Session

from modules.imports.dependencies import (  # noqa: E402
    AuthContext,
    InMemoryAnalysisLookup,
    InMemoryAuditLogger,
)
from modules.imports.dependencies import AnalysisStatus  # noqa: E402
from modules.imports.exceptions import (  # noqa: E402
    AnalysisNotAcceptingUploadsError,
    AnalysisNotFoundError,
    DuplicateFileError,
    EmptyFileError,
    FileTooLargeError,
    InvalidFileTypeError,
    UnauthorizedBranchAccessError,
)
from modules.imports.models import Base, FileStatus, SourceType  # noqa: E402
from modules.imports.security import NoOpMalwareScanner  # noqa: E402
from modules.imports.service import ImportService  # noqa: E402
from modules.imports.storage import LocalEncryptedFileStorage  # noqa: E402

BRANCH_ID = "branch-1"
USER_ID = "user-1"
ANALYSIS_ID = "analysis-1"

# UploadedFile declares a real ForeignKey to "analyses", which is now a
# real table (Analysis Orchestration). Importing its model registers that
# table for real, in place of the stand-in this file used before that
# module existed. "users" and "branches" still don't have real models
# (Identity & Access, Organization & Branch Management aren't built yet),
# so those stay as minimal stand-ins solely for this test suite's schema.
from modules.analysis_orchestration.models import Analysis  # noqa: E402,F401

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
def storage(tmp_path):
    return LocalEncryptedFileStorage(tmp_path / "files", Fernet.generate_key())


@pytest.fixture()
def analysis_lookup():
    lookup = InMemoryAnalysisLookup()
    lookup.register(ANALYSIS_ID, AnalysisStatus.AWAITING_FILES, BRANCH_ID)
    return lookup


@pytest.fixture()
def audit_logger():
    return InMemoryAuditLogger()


@pytest.fixture()
def auth():
    return AuthContext(user_id=USER_ID, accessible_branch_ids=frozenset({BRANCH_ID}))


@pytest.fixture()
def service(db, storage, analysis_lookup, audit_logger):
    return ImportService(
        db=db,
        storage=storage,
        analysis_lookup=analysis_lookup,
        audit_logger=audit_logger,
        scanner=NoOpMalwareScanner(),
        max_size_bytes=1024,  # small limit to make the "too large" test cheap
    )


def _upload(service, auth, content=b"order_id,amount\n1,10.00\n", filename="pos_export.csv", source_type=SourceType.POS_EXPORT):
    return service.import_file(
        analysis_id=ANALYSIS_ID,
        source_type=source_type,
        original_filename=filename,
        content=content,
        auth=auth,
    )


class TestSuccessfulImport:
    def test_creates_a_received_record(self, service, auth):
        record = _upload(service, auth)
        assert record.status == FileStatus.RECEIVED
        assert record.version == 1
        assert record.analysis_id == ANALYSIS_ID

    def test_stores_content_encrypted_and_retrievable(self, service, auth, storage):
        content = b"order_id,amount\n1,10.00\n"
        record = _upload(service, auth, content=content)
        raw_on_disk = (storage._base_dir / record.storage_path).read_bytes()
        assert raw_on_disk != content  # not stored as plaintext
        assert storage.read(record.storage_path) == content  # decrypts correctly

    def test_logs_a_success_audit_event(self, service, auth, audit_logger):
        _upload(service, auth)
        assert audit_logger.records[-1].event == "file_import_succeeded"


class TestReuploadVersioning:
    def test_second_upload_supersedes_the_first(self, service, auth, db):
        first = _upload(service, auth, content=b"a" * 10)
        second = _upload(service, auth, content=b"b" * 10)

        db.refresh(first)
        assert first.status == FileStatus.SUPERSEDED
        assert first.superseded_at is not None
        assert second.status == FileStatus.RECEIVED
        assert second.version == first.version + 1


class TestDuplicateDetection:
    def test_identical_content_rejected_as_duplicate(self, service, auth):
        content = b"same content"
        _upload(service, auth, content=content)
        with pytest.raises(DuplicateFileError):
            _upload(service, auth, content=content)

    def test_duplicate_check_is_scoped_per_analysis(self, service, auth, analysis_lookup, db):
        other_analysis = "analysis-2"
        analysis_lookup.register(other_analysis, AnalysisStatus.AWAITING_FILES, BRANCH_ID)
        content = b"shared content"
        _upload(service, auth, content=content)
        # Same bytes, different analysis - must NOT be treated as a duplicate.
        record = service.import_file(
            analysis_id=other_analysis,
            source_type=SourceType.POS_EXPORT,
            original_filename="pos_export.csv",
            content=content,
            auth=auth,
        )
        assert record.status == FileStatus.RECEIVED


class TestFileLevelGuardrails:
    def test_disallowed_extension_rejected(self, service, auth):
        with pytest.raises(InvalidFileTypeError):
            _upload(service, auth, filename="export.exe")

    def test_empty_file_rejected(self, service, auth):
        with pytest.raises(EmptyFileError):
            _upload(service, auth, content=b"")

    def test_oversized_file_rejected(self, service, auth):
        with pytest.raises(FileTooLargeError):
            _upload(service, auth, content=b"x" * 2048)  # over the 1024-byte test limit


class TestAnalysisStateChecks:
    def test_unknown_analysis_rejected(self, service, auth):
        with pytest.raises(AnalysisNotFoundError):
            service.import_file(
                analysis_id="does-not-exist",
                source_type=SourceType.POS_EXPORT,
                original_filename="pos_export.csv",
                content=b"data",
                auth=auth,
            )

    def test_completed_analysis_rejects_new_uploads(self, service, auth, analysis_lookup):
        analysis_lookup.register(ANALYSIS_ID, AnalysisStatus.COMPLETED, BRANCH_ID)
        with pytest.raises(AnalysisNotAcceptingUploadsError):
            _upload(service, auth)


class TestAuthorization:
    def test_user_without_branch_access_is_rejected(self, service):
        auth = AuthContext(user_id=USER_ID, accessible_branch_ids=frozenset({"some-other-branch"}))
        with pytest.raises(UnauthorizedBranchAccessError):
            _upload(service, auth)


class TestFilenameSafety:
    def test_path_traversal_attempt_is_sanitized(self, service, auth, storage):
        record = _upload(service, auth, filename="../../etc/passwd.csv")
        # The sanitized path used on disk must stay inside storage's base dir.
        resolved = (storage._base_dir / record.storage_path).resolve()
        assert storage._base_dir.resolve() in resolved.parents
        # The original (unsafe) filename is still preserved for display only.
        assert record.original_filename == "../../etc/passwd.csv"
