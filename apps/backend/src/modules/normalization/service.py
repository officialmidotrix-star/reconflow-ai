"""
Core business logic for the Data Normalization module.

`NormalizationService.normalize_file` reads a file Data Validation has
already passed, and produces canonical Transaction rows from it. Re-running
normalization on the same uploaded file supersedes its previous
transactions (delete-and-replace within one commit) rather than
duplicating them - the same idempotency discipline Data Import uses for
re-uploads.
"""

from __future__ import annotations

import uuid
from datetime import timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from modules.imports.models import SourceType, UploadedFile
from modules.imports.security import file_extension
from modules.imports.storage import FileStorage
from modules.validation.models import FileValidation, ValidationStatus
from modules.validation.parsers import FileParseError, RowCapExceededError, parser_for_extension
from modules.validation.schema_registry import parse_date, parse_decimal, schema_for

from .dependencies import AnalysisTimezoneLookup, AuditLogger
from .exceptions import (
    BranchConfigurationMissingError,
    FileNotValidatedError,
    FileReadError,
    NormalizationInternalError,
    NormalizationPersistError,
    UploadedFileNotFoundError,
)
from .models import NormalizationRun, NormalizationStatus, NormalizationWarning, Transaction, TransactionOrderStatus

DEFAULT_MAX_ROWS = 250_000  # same safety cap rationale as Data Validation

# Which schema field represents the transaction's primary timestamp and
# amount differs by source type - this is the one place that mapping
# lives, so a new source type only needs an entry added here.
TIMESTAMP_FIELD_BY_SOURCE: dict[SourceType, str] = {
    SourceType.POS_EXPORT: "order_time",
    SourceType.PLATFORM_SETTLEMENT: "settlement_date",
}
AMOUNT_FIELD_BY_SOURCE: dict[SourceType, str] = {
    SourceType.POS_EXPORT: "amount",
    SourceType.PLATFORM_SETTLEMENT: "gross_amount",
}
# Only PLATFORM_SETTLEMENT rows carry a commission figure - POS_EXPORT has
# no equivalent field, so it's absent from this mapping rather than mapped
# to None, keeping "does this source type have a commission field at all"
# an explicit lookup (`.get(...)`) rather than a magic sentinel value.
COMMISSION_FIELD_BY_SOURCE: dict[SourceType, str] = {
    SourceType.PLATFORM_SETTLEMENT: "commission_amount",
}
REFERENCE_FIELD = "order_id"
# Present in positions only for POS_EXPORT files that actually had the
# column - schema_for(PLATFORM_SETTLEMENT) has no order_status spec at
# all, so positions.get() naturally returns None there regardless of
# file content, with no per-source-type lookup needed the way
# COMMISSION_FIELD_BY_SOURCE needs one.
ORDER_STATUS_FIELD = "order_status"


class NormalizationService:
    def __init__(
        self,
        *,
        db: Session,
        storage: FileStorage,
        tz_lookup: AnalysisTimezoneLookup,
        audit_logger: AuditLogger,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> None:
        self._db = db
        self._storage = storage
        self._tz_lookup = tz_lookup
        self._audit_logger = audit_logger
        self._max_rows = max_rows

    def normalize_file(self, *, uploaded_file_id: str, requested_by: str) -> NormalizationRun:
        uploaded_file = self._db.get(UploadedFile, uploaded_file_id)
        if uploaded_file is None:
            raise UploadedFileNotFoundError("We couldn't find that uploaded file.")

        self._check_validated(uploaded_file_id)

        tz_name = self._tz_lookup.get_timezone(uploaded_file.analysis_id)
        currency = self._tz_lookup.get_currency(uploaded_file.analysis_id)
        if not tz_name or not currency:
            raise BranchConfigurationMissingError(
                "This branch doesn't have a timezone and currency configured yet."
            )
        tzinfo = ZoneInfo(tz_name)

        content = self._read_content(uploaded_file)
        parser = self._parser_for(uploaded_file)
        schema = schema_for(uploaded_file.source_type)
        timestamp_field = TIMESTAMP_FIELD_BY_SOURCE[uploaded_file.source_type]
        amount_field = AMOUNT_FIELD_BY_SOURCE[uploaded_file.source_type]
        commission_field = COMMISSION_FIELD_BY_SOURCE.get(uploaded_file.source_type)

        transactions: list[Transaction] = []
        warnings: list[tuple[int | None, str | None, str]] = []
        order_statuses: list[tuple[str, str]] = []

        rows_iter = parser.iter_rows(content, max_rows=self._max_rows)
        try:
            header = self._read_header_or_raise(rows_iter)
            positions = self._match_columns_or_raise(header, schema)
            self._build_transactions(
                rows_iter=rows_iter,
                positions=positions,
                reference_field=REFERENCE_FIELD,
                timestamp_field=timestamp_field,
                amount_field=amount_field,
                commission_field=commission_field,
                tzinfo=tzinfo,
                uploaded_file=uploaded_file,
                currency=currency,
                transactions=transactions,
                warnings=warnings,
                order_statuses=order_statuses,
            )
        finally:
            rows_iter.close()

        return self._persist(uploaded_file, transactions, warnings, order_statuses, requested_by)

    # -- internal steps -------------------------------------------------

    def _check_validated(self, uploaded_file_id: str) -> None:
        latest = self._db.execute(
            select(FileValidation)
            .where(FileValidation.uploaded_file_id == uploaded_file_id)
            .order_by(FileValidation.checked_at.desc())
        ).scalars().first()
        if latest is None or latest.status != ValidationStatus.PASSED:
            raise FileNotValidatedError(
                "This file needs to pass validation before it can be normalized."
            )

    def _read_content(self, uploaded_file: UploadedFile) -> bytes:
        try:
            return self._storage.read(uploaded_file.storage_path)
        except Exception as exc:  # noqa: BLE001
            raise FileReadError(
                "We couldn't re-read the stored file for normalization."
            ) from exc

    def _parser_for(self, uploaded_file: UploadedFile):
        try:
            return parser_for_extension(file_extension(uploaded_file.original_filename))
        except FileParseError as exc:
            raise FileReadError("This file's type is no longer supported.") from exc

    def _read_header_or_raise(self, rows_iter) -> list[str]:
        try:
            return next(rows_iter)
        except (StopIteration, FileParseError, RowCapExceededError) as exc:
            # Should not happen given the PASSED-validation precondition -
            # if it does, the stored file changed or is inconsistent.
            raise FileReadError(
                "The stored file could not be re-read for normalization."
            ) from exc

    def _match_columns_or_raise(self, header: list[str], schema) -> dict[str, int]:
        positions: dict[str, int] = {}
        for col in schema:
            idx = next((i for i, h in enumerate(header) if col.matches_header(h)), None)
            if idx is None:
                if not col.is_required:
                    continue  # optional and absent - fine, no position to record
                # Should not happen given PASSED validation - see docstring
                # in exceptions.NormalizationInternalError.
                raise NormalizationInternalError(
                    f"Expected column '{col.display_name}' was missing on re-read, "
                    "despite passing validation."
                )
            positions[col.field_name] = idx
        return positions

    def _build_transactions(
        self,
        *,
        rows_iter,
        positions: dict[str, int],
        reference_field: str,
        timestamp_field: str,
        amount_field: str,
        commission_field: str | None,
        tzinfo: ZoneInfo,
        uploaded_file: UploadedFile,
        currency: str,
        transactions: list[Transaction],
        warnings: list[tuple[int | None, str | None, str]],
        order_statuses: list[tuple[str, str]],
    ) -> None:
        seen_references: dict[str, int] = {}
        order_status_idx = positions.get(ORDER_STATUS_FIELD)
        try:
            for row_number, row in enumerate(rows_iter, start=2):  # header was row 1
                reference = self._cell(row, positions[reference_field]).strip()
                raw_timestamp = self._cell(row, positions[timestamp_field])
                raw_amount = self._cell(row, positions[amount_field])

                naive_dt = parse_date(raw_timestamp)
                amount = parse_decimal(raw_amount)
                if naive_dt is None or amount is None:
                    raise NormalizationInternalError(
                        f"Row {row_number} failed to parse during normalization "
                        "despite passing validation."
                    )

                platform_commission_amount = None
                if commission_field is not None:
                    raw_commission = self._cell(row, positions[commission_field])
                    commission = parse_decimal(raw_commission)
                    if commission is None:
                        raise NormalizationInternalError(
                            f"Row {row_number}: '{commission_field}' failed to parse during "
                            "normalization despite passing validation."
                        )
                    platform_commission_amount = commission.quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )

                occurred_at = naive_dt.replace(tzinfo=tzinfo).astimezone(timezone.utc)
                quantized_amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                if reference in seen_references:
                    warnings.append(
                        (
                            row_number,
                            "external_reference",
                            f"Duplicate reference '{reference}' also appears on row "
                            f"{seen_references[reference]}.",
                        )
                    )
                else:
                    seen_references[reference] = row_number

                # Generated here rather than left to the column's default -
                # order_statuses needs a real transaction_id to reference
                # before this session flushes, and a Python-side
                # mapped_column default isn't guaranteed to have run yet
                # at this point (it's evaluated at flush/INSERT time).
                transaction_id = str(uuid.uuid4())
                transactions.append(
                    Transaction(
                        id=transaction_id,
                        analysis_id=uploaded_file.analysis_id,
                        uploaded_file_id=uploaded_file.id,
                        source_type=uploaded_file.source_type,
                        external_reference=reference,
                        occurred_at=occurred_at,
                        amount=quantized_amount,
                        currency_code=currency,
                        platform_commission_amount=platform_commission_amount,
                    )
                )

                if order_status_idx is not None:
                    raw_status = self._cell(row, order_status_idx).strip()
                    if raw_status:
                        order_statuses.append((transaction_id, raw_status))
        except RowCapExceededError as exc:
            raise FileReadError(
                f"The file exceeded {exc.max_rows:,} rows during normalization re-read."
            ) from exc

    @staticmethod
    def _cell(row: list[str], index: int) -> str:
        return row[index] if index < len(row) else ""

    def _persist(
        self,
        uploaded_file: UploadedFile,
        transactions: list[Transaction],
        warnings: list[tuple[int | None, str | None, str]],
        order_statuses: list[tuple[str, str]],
        requested_by: str,
    ) -> NormalizationRun:
        # Supersede: replace any transactions from a previous normalization
        # run of this same uploaded file, rather than duplicating them.
        # transaction_order_statuses.transaction_id has ondelete=CASCADE,
        # so the DB itself cleans up any orphaned status rows here - no
        # separate delete needed, and this bulk Core delete wouldn't
        # trigger ORM-level cascade even if the FK didn't have it set.
        self._db.execute(delete(Transaction).where(Transaction.uploaded_file_id == uploaded_file.id))
        for txn in transactions:
            self._db.add(txn)
        for transaction_id, raw_status in order_statuses:
            self._db.add(TransactionOrderStatus(transaction_id=transaction_id, raw_status=raw_status))

        run = NormalizationRun(
            uploaded_file_id=uploaded_file.id,
            status=NormalizationStatus.COMPLETED,
            rows_created=len(transactions),
        )
        for row_number, field_name, message in warnings:
            run.warnings.append(
                NormalizationWarning(row_number=row_number, field=field_name, message=message)
            )
        self._db.add(run)

        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise NormalizationPersistError(
                "We couldn't save the normalization result. Please try again."
            ) from exc
        self._db.refresh(run)

        self._audit_logger.log(
            event="file_normalization_completed",
            user_id=requested_by,
            analysis_id=uploaded_file.analysis_id,
            metadata={
                "uploaded_file_id": uploaded_file.id,
                "rows_created": len(transactions),
                "warning_count": len(warnings),
            },
        )
        return run
