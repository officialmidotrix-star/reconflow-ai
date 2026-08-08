"""
Unit tests for ValidationService.

Builds real UploadedFile rows (via Data Import's own model) backed by real
encrypted storage, then runs the actual Data Validation parsing/checking
logic against them - CSV and XLSX content is genuine, not mocked. The only
stand-ins are for modules that still don't exist (Audit Logging, and the
analyses/users tables Data Import itself depends on).
"""

from __future__ import annotations

import io

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, String, Table, create_engine
from sqlalchemy.orm import Session

from modules.imports.models import Base, FileStatus
from modules.imports.models import SourceType
from modules.imports.models import UploadedFile
from modules.imports.security import compute_checksum, file_extension
from modules.imports.storage import LocalEncryptedFileStorage
from modules.validation.dependencies import InMemoryAuditLogger
from modules.validation.exceptions import UploadedFileNotFoundError
from modules.validation.models import IssueCode, ValidationStatus
from modules.validation.service import ValidationService

ANALYSIS_ID = "analysis-1"
USER_ID = "user-1"

# See tests/backend/modules/imports/test_service.py for why these stand-ins
# exist and why it's safe to declare them again here (idempotent guard).
# "analyses" is now a real table (Analysis Orchestration) - importing its
# model registers it, replacing the stand-in this file used before that
# module existed. "branches" and "users" still don't have real models.
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
def audit_logger():
    return InMemoryAuditLogger()


@pytest.fixture()
def service(db, storage, audit_logger):
    return ValidationService(db=db, storage=storage, audit_logger=audit_logger)


def _make_uploaded_file(
    db: Session,
    storage: LocalEncryptedFileStorage,
    *,
    content: bytes,
    filename: str,
    source_type: SourceType,
) -> UploadedFile:
    ext = file_extension(filename)
    storage_path = f"branch-1/{ANALYSIS_ID}/{source_type.value}/v1.{ext}"
    storage.save(storage_path, content)
    record = UploadedFile(
        analysis_id=ANALYSIS_ID,
        source_type=source_type,
        original_filename=filename,
        storage_path=storage_path,
        checksum_sha256=compute_checksum(content),
        size_bytes=len(content),
        status=FileStatus.RECEIVED,
        version=1,
        uploaded_by=USER_ID,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _make_xlsx_bytes(rows: list[list[str]]) -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class TestValidPosExport:
    def test_passes_with_valid_data(self, service, db, storage):
        content = b"order_id,order_time,amount\n1001,2026-01-15,42.50\n"
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.PASSED
        assert result.row_count == 1
        assert result.issues == []

    def test_optional_order_status_column_is_accepted_when_present(self, service, db, storage):
        content = (
            b"order_id,order_time,amount,order_status\n"
            b"1001,2026-01-15,42.50,CANCELLED\n"
            b"1002,2026-01-15,18.00,\n"  # present but empty for this row - fine, it's optional
        )
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.PASSED
        assert result.row_count == 2
        assert result.issues == []

    def test_amount_with_thousands_separator_and_quoting_is_valid(self, service, db, storage):
        # A comma inside an unquoted CSV field would split into two columns -
        # that's a real-world export mistake, not something this validator
        # should paper over. A properly quoted field is the correct way for
        # an export to include a comma, and should parse as one valid amount.
        content = b'order_id,order_time,amount\n1001,2026-01-15,"1,234.56"\n'
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.PASSED
        assert result.row_count == 1


class TestValidPlatformSettlement:
    def test_passes_with_valid_data(self, service, db, storage):
        content = (
            b"order_id,settlement_date,gross_amount,commission_amount\n"
            b"1001,2026-01-15,42.50,6.38\n"
        )
        uploaded = _make_uploaded_file(
            db,
            storage,
            content=content,
            filename="settlement.csv",
            source_type=SourceType.PLATFORM_SETTLEMENT,
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.PASSED
        assert result.row_count == 1


class TestMissingColumns:
    def test_missing_required_column_reported_by_name(self, service, db, storage):
        content = b"order_id,order_time\n1001,2026-01-15\n"  # amount missing
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.FAILED
        codes = [i.issue_code for i in result.issues]
        assert IssueCode.MISSING_COLUMN in codes
        messages = [i.message for i in result.issues]
        assert any("Amount" in m for m in messages)


class TestRowLevelIssues:
    def test_invalid_amount_reported_with_row_number(self, service, db, storage):
        content = b"order_id,order_time,amount\n1001,2026-01-15,not-a-number\n"
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.FAILED
        issue = next(i for i in result.issues if i.issue_code == IssueCode.INVALID_NUMBER)
        assert issue.row_number == 2  # header is row 1

    def test_invalid_date_reported(self, service, db, storage):
        content = b"order_id,order_time,amount\n1001,not-a-date,42.50\n"
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert any(i.issue_code == IssueCode.INVALID_DATE for i in result.issues)

    def test_collects_multiple_issues_in_one_pass(self, service, db, storage):
        content = (
            b"order_id,order_time,amount\n"
            b"1001,not-a-date,42.50\n"
            b"1002,2026-01-15,not-a-number\n"
        )
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        codes = {i.issue_code for i in result.issues}
        assert IssueCode.INVALID_DATE in codes
        assert IssueCode.INVALID_NUMBER in codes
        assert result.row_count == 2  # both rows still counted


class TestNoDataRows:
    def test_header_only_file_fails(self, service, db, storage):
        content = b"order_id,order_time,amount\n"
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.FAILED
        assert any(i.issue_code == IssueCode.NO_DATA_ROWS for i in result.issues)


class TestHeaderMatchingIsForgiving:
    def test_case_and_spacing_insensitive_headers(self, service, db, storage):
        content = b"Order ID,Order Date,Total\n1001,2026-01-15,42.50\n"
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.PASSED


class TestXlsxSupport:
    def test_valid_xlsx_passes(self, service, db, storage):
        content = _make_xlsx_bytes(
            [["order_id", "order_time", "amount"], ["1001", "2026-01-15", "42.50"]]
        )
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.xlsx", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.PASSED
        assert result.row_count == 1

    def test_corrupted_xlsx_fails_gracefully(self, service, db, storage):
        uploaded = _make_uploaded_file(
            db,
            storage,
            content=b"this is not a real xlsx file",
            filename="pos.xlsx",
            source_type=SourceType.POS_EXPORT,
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.FAILED
        assert any(i.issue_code == IssueCode.UNREADABLE_FILE for i in result.issues)


class TestRowCap:
    def test_row_cap_is_enforced(self, db, storage, audit_logger):
        service = ValidationService(db=db, storage=storage, audit_logger=audit_logger, max_rows=2)
        content = b"order_id,order_time,amount\n1,2026-01-15,1\n2,2026-01-15,2\n3,2026-01-15,3\n"
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        result = service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert result.status == ValidationStatus.FAILED
        assert any(i.issue_code == IssueCode.TOO_MANY_ROWS for i in result.issues)


class TestNotFound:
    def test_unknown_uploaded_file_raises(self, service):
        with pytest.raises(UploadedFileNotFoundError):
            service.validate_file(uploaded_file_id="does-not-exist", requested_by=USER_ID)


class TestAuditLogging:
    def test_logs_outcome(self, service, db, storage, audit_logger):
        content = b"order_id,order_time,amount\n1001,2026-01-15,42.50\n"
        uploaded = _make_uploaded_file(
            db, storage, content=content, filename="pos.csv", source_type=SourceType.POS_EXPORT
        )
        service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert audit_logger.records[-1].event == "file_validation_passed"
        assert audit_logger.records[-1].analysis_id == ANALYSIS_ID
