"""
Unit tests for NormalizationService.

Builds real UploadedFile rows and runs the real ValidationService against
them first (so the PASSED FileValidation precondition is genuine, not
faked), then exercises NormalizationService against that state. Only the
branch timezone/currency lookup and audit logging are stand-ins, for the
same reason as every other module so far: those owning modules don't
exist yet.
"""

from __future__ import annotations

from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, String, Table, create_engine, select
from sqlalchemy.orm import Session

from modules.imports.models import Base, FileStatus, SourceType, UploadedFile
from modules.imports.security import compute_checksum, file_extension
from modules.imports.storage import LocalEncryptedFileStorage
from modules.normalization.dependencies import InMemoryAnalysisTimezoneLookup, InMemoryAuditLogger
from modules.normalization.exceptions import (
    BranchConfigurationMissingError,
    FileNotValidatedError,
    UploadedFileNotFoundError,
)
from modules.normalization.models import NormalizationStatus, Transaction, TransactionOrderStatus
from modules.normalization.service import NormalizationService
from modules.validation.dependencies import InMemoryAuditLogger as ValidationAuditLogger
from modules.validation.service import ValidationService

ANALYSIS_ID = "analysis-1"
USER_ID = "user-1"
TIMEZONE_NAME = "Asia/Riyadh"  # UTC+3, no DST - deterministic for assertions
CURRENCY = "SAR"

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
def tz_lookup():
    lookup = InMemoryAnalysisTimezoneLookup()
    lookup.register(ANALYSIS_ID, TIMEZONE_NAME, CURRENCY)
    return lookup


@pytest.fixture()
def audit_logger():
    return InMemoryAuditLogger()


@pytest.fixture()
def validation_service(db, storage):
    return ValidationService(db=db, storage=storage, audit_logger=ValidationAuditLogger())


@pytest.fixture()
def service(db, storage, tz_lookup, audit_logger):
    return NormalizationService(db=db, storage=storage, tz_lookup=tz_lookup, audit_logger=audit_logger)


def _upload_and_validate(
    db: Session,
    storage: LocalEncryptedFileStorage,
    validation_service: ValidationService,
    *,
    content: bytes,
    filename: str,
    source_type: SourceType,
) -> UploadedFile:
    ext = file_extension(filename)
    storage_path = f"branch-1/{ANALYSIS_ID}/{source_type.value}/v1.{ext}"
    storage.save(storage_path, content)
    uploaded = UploadedFile(
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
    db.add(uploaded)
    db.commit()
    db.refresh(uploaded)
    validation_service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
    return uploaded


class TestPosExportNormalization:
    def test_normalizes_correctly(self, service, db, storage, validation_service):
        content = b"order_id,order_time,amount\n1001,2026-01-15 10:00:00,42.50\n"
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        run = service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)

        assert run.status == NormalizationStatus.COMPLETED
        assert run.rows_created == 1

        txn = db.execute(select(Transaction).where(Transaction.uploaded_file_id == uploaded.id)).scalar_one()
        assert txn.external_reference == "1001"
        assert txn.amount == Decimal("42.50")
        assert txn.currency_code == "SAR"
        # 2026-01-15 10:00 in Asia/Riyadh (UTC+3) -> 2026-01-15 07:00 UTC
        assert txn.occurred_at.astimezone(dt_timezone.utc).hour == 7
        assert txn.occurred_at.tzinfo is not None


class TestOrderStatusPersistence:
    def test_order_status_persisted_when_column_present_and_populated(
        self, service, db, storage, validation_service
    ):
        content = (
            b"order_id,order_time,amount,order_status\n"
            b"1001,2026-01-15 10:00:00,42.50,Cancelled by customer\n"
        )
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)

        txn = db.execute(select(Transaction).where(Transaction.uploaded_file_id == uploaded.id)).scalar_one()
        status = db.execute(
            select(TransactionOrderStatus).where(TransactionOrderStatus.transaction_id == txn.id)
        ).scalar_one()
        assert status.raw_status == "Cancelled by customer"

    def test_no_status_row_created_when_column_absent(self, service, db, storage, validation_service):
        content = b"order_id,order_time,amount\n1001,2026-01-15 10:00:00,42.50\n"
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)

        txn = db.execute(select(Transaction).where(Transaction.uploaded_file_id == uploaded.id)).scalar_one()
        status = db.execute(
            select(TransactionOrderStatus).where(TransactionOrderStatus.transaction_id == txn.id)
        ).scalar_one_or_none()
        assert status is None

    def test_no_status_row_created_when_column_present_but_empty_for_that_row(
        self, service, db, storage, validation_service
    ):
        content = b"order_id,order_time,amount,order_status\n1001,2026-01-15 10:00:00,42.50,\n"
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)

        txn = db.execute(select(Transaction).where(Transaction.uploaded_file_id == uploaded.id)).scalar_one()
        status = db.execute(
            select(TransactionOrderStatus).where(TransactionOrderStatus.transaction_id == txn.id)
        ).scalar_one_or_none()
        assert status is None

    def test_rerun_supersedes_rather_than_duplicates(self, service, db, storage, validation_service):
        # This checks the Transaction-level supersede, not the FK cascade
        # itself - SQLite doesn't enforce foreign keys unless explicitly
        # told to, and properly exercising ondelete=CASCADE here would
        # need a full valid User/Organization/Branch/Analysis chain
        # hand-built just for one test. Verified live against real
        # Postgres instead, where that chain already exists naturally
        # from actually running the pipeline.
        content = (
            b"order_id,order_time,amount,order_status\n"
            b"1001,2026-01-15 10:00:00,42.50,CANCELLED\n"
        )
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)  # rerun

        txns = db.execute(select(Transaction).where(Transaction.uploaded_file_id == uploaded.id)).scalars().all()
        assert len(txns) == 1


class TestPlatformSettlementNormalization:
    def test_uses_gross_amount_as_transaction_amount(self, service, db, storage, validation_service):
        content = (
            b"order_id,settlement_date,gross_amount,commission_amount\n"
            b"2001,2026-01-15 12:00:00,100.00,15.00\n"
        )
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="settlement.csv",
            source_type=SourceType.PLATFORM_SETTLEMENT,
        )
        run = service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)

        assert run.rows_created == 1
        txn = db.execute(select(Transaction).where(Transaction.uploaded_file_id == uploaded.id)).scalar_one()
        assert txn.amount == Decimal("100.00")
        assert txn.platform_commission_amount == Decimal("15.00")


class TestCommissionAmountCapture:
    def test_pos_export_has_no_commission_amount(self, service, db, storage, validation_service):
        content = b"order_id,order_time,amount\n1001,2026-01-15,42.50\n"
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        txn = db.execute(select(Transaction).where(Transaction.uploaded_file_id == uploaded.id)).scalar_one()
        assert txn.platform_commission_amount is None


class TestAmountQuantization:
    def test_amount_quantized_to_two_decimal_places(self, service, db, storage, validation_service):
        content = b"order_id,order_time,amount\n1001,2026-01-15,42.5\n"
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        txn = db.execute(select(Transaction).where(Transaction.uploaded_file_id == uploaded.id)).scalar_one()
        assert txn.amount == Decimal("42.50")


class TestPrecondition:
    def test_fails_when_never_validated(self, service, db, storage):
        content = b"order_id,order_time,amount\n1001,2026-01-15,42.50\n"
        storage.save(f"branch-1/{ANALYSIS_ID}/POS_EXPORT/v1.csv", content)
        uploaded = UploadedFile(
            analysis_id=ANALYSIS_ID,
            source_type=SourceType.POS_EXPORT,
            original_filename="pos.csv",
            storage_path=f"branch-1/{ANALYSIS_ID}/POS_EXPORT/v1.csv",
            checksum_sha256=compute_checksum(content),
            size_bytes=len(content),
            status=FileStatus.RECEIVED,
            version=1,
            uploaded_by=USER_ID,
        )
        db.add(uploaded)
        db.commit()
        db.refresh(uploaded)

        with pytest.raises(FileNotValidatedError):
            service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)

    def test_fails_when_validation_failed(self, service, db, storage, validation_service):
        content = b"order_id,order_time\n1001,2026-01-15\n"  # missing amount -> FAILED
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        with pytest.raises(FileNotValidatedError):
            service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)


class TestRerunSupersedes:
    def test_second_run_replaces_first_runs_transactions(self, service, db, storage, validation_service):
        content_v1 = b"order_id,order_time,amount\n1001,2026-01-15,42.50\n"
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content_v1, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)

        # Re-normalize the same uploaded file (e.g. re-run after a config
        # change) - should replace, not duplicate.
        service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)

        count = db.execute(
            select(Transaction).where(Transaction.uploaded_file_id == uploaded.id)
        ).scalars().all()
        assert len(count) == 1


class TestDuplicateReferenceWarning:
    def test_duplicate_reference_recorded_as_warning_not_a_failure(
        self, service, db, storage, validation_service
    ):
        content = (
            b"order_id,order_time,amount\n"
            b"1001,2026-01-15,42.50\n"
            b"1001,2026-01-15,10.00\n"
        )
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        run = service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)

        assert run.status == NormalizationStatus.COMPLETED
        assert run.rows_created == 2  # both rows still created
        assert len(run.warnings) == 1
        assert "Duplicate reference" in run.warnings[0].message


class TestBranchConfigurationMissing:
    def test_raises_when_timezone_not_configured(self, db, storage, audit_logger, validation_service):
        service = NormalizationService(
            db=db, storage=storage, tz_lookup=InMemoryAnalysisTimezoneLookup(), audit_logger=audit_logger
        )
        content = b"order_id,order_time,amount\n1001,2026-01-15,42.50\n"
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        with pytest.raises(BranchConfigurationMissingError):
            service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)


class TestNotFound:
    def test_unknown_uploaded_file_raises(self, service):
        with pytest.raises(UploadedFileNotFoundError):
            service.normalize_file(uploaded_file_id="does-not-exist", requested_by=USER_ID)


class TestRowCountMatchesValidation:
    def test_transaction_count_matches_validation_row_count(
        self, service, db, storage, validation_service
    ):
        content = (
            b"order_id,order_time,amount\n"
            b"1001,2026-01-15,10.00\n"
            b"1002,2026-01-16,20.00\n"
            b"1003,2026-01-17,30.00\n"
        )
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        validation = validation_service.validate_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        run = service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert run.rows_created == validation.row_count == 3


class TestAuditLogging:
    def test_logs_completion(self, service, db, storage, validation_service, audit_logger):
        content = b"order_id,order_time,amount\n1001,2026-01-15,42.50\n"
        uploaded = _upload_and_validate(
            db, storage, validation_service, content=content, filename="pos.csv",
            source_type=SourceType.POS_EXPORT,
        )
        service.normalize_file(uploaded_file_id=uploaded.id, requested_by=USER_ID)
        assert audit_logger.records[-1].event == "file_normalization_completed"
        assert audit_logger.records[-1].analysis_id == ANALYSIS_ID
