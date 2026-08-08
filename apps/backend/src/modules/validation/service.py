"""
Core business logic for the Data Validation module.

`ValidationService.validate_file` always produces a FileValidation record -
PASSED or FAILED - it never raises just because a file turns out to be
invalid. Exceptions here are reserved for requests that can't be carried
out at all (no such uploaded file, storage unreadable, DB write failed).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from modules.imports.models import UploadedFile
from modules.imports.security import file_extension
from modules.imports.storage import FileStorage

from .dependencies import AuditLogger
from .exceptions import FileReadError, UploadedFileNotFoundError, ValidationPersistError
from .models import FileValidation, IssueCode, ValidationIssue, ValidationStatus
from .parsers import FileParseError, RowCapExceededError, parser_for_extension
from .schema_registry import schema_for

DEFAULT_MAX_ROWS = 250_000  # headroom above the 100k+ transaction target,
# enforced independently of Data Import's byte-size cap - see design doc
# section 10 (XLSX decompression-bomb risk).
DEFAULT_MAX_ISSUES = 100  # cap how many issues we collect per run so one
# badly-formed file doesn't produce an unusably long report; row_count
# still reflects every row actually seen.

_Issue = tuple[int | None, str | None, IssueCode, str]


class ValidationService:
    def __init__(
        self,
        *,
        db: Session,
        storage: FileStorage,
        audit_logger: AuditLogger,
        max_rows: int = DEFAULT_MAX_ROWS,
        max_issues: int = DEFAULT_MAX_ISSUES,
    ) -> None:
        self._db = db
        self._storage = storage
        self._audit_logger = audit_logger
        self._max_rows = max_rows
        self._max_issues = max_issues

    def validate_file(self, *, uploaded_file_id: str, requested_by: str) -> FileValidation:
        uploaded_file = self._db.get(UploadedFile, uploaded_file_id)
        if uploaded_file is None:
            raise UploadedFileNotFoundError("We couldn't find that uploaded file.")

        try:
            content = self._storage.read(uploaded_file.storage_path)
        except Exception as exc:  # noqa: BLE001
            raise FileReadError(
                "We couldn't read the stored file. Please try uploading it again."
            ) from exc

        issues: list[_Issue] = []
        row_count = 0
        status = ValidationStatus.PASSED

        extension = file_extension(uploaded_file.original_filename)
        try:
            parser = parser_for_extension(extension)
        except FileParseError:
            issues.append(
                (None, None, IssueCode.UNREADABLE_FILE, f"Unsupported file type: .{extension}")
            )
            status = ValidationStatus.FAILED
            return self._finish(uploaded_file, status, row_count, issues, requested_by)

        rows_iter = parser.iter_rows(content, max_rows=self._max_rows)
        try:
            header = self._read_header(rows_iter, issues)
            if header is not None:
                column_positions = self._match_columns(header, uploaded_file, issues)
                if column_positions is not None:
                    row_count = self._check_rows(rows_iter, column_positions, uploaded_file, issues)
                    if row_count == 0:
                        issues.append(
                            (
                                None,
                                None,
                                IssueCode.NO_DATA_ROWS,
                                "This file has a header row but no data rows.",
                            )
                        )
        finally:
            rows_iter.close()

        if issues:
            status = ValidationStatus.FAILED

        return self._finish(uploaded_file, status, row_count, issues, requested_by)

    # -- internal steps -------------------------------------------------

    def _read_header(self, rows_iter, issues: list[_Issue]) -> list[str] | None:
        try:
            return next(rows_iter)
        except StopIteration:
            issues.append(
                (None, None, IssueCode.NO_DATA_ROWS, "This file appears to be empty.")
            )
        except FileParseError:
            issues.append(
                (
                    None,
                    None,
                    IssueCode.UNREADABLE_FILE,
                    "We couldn't read this file. It may be corrupted - please re-export and try again.",
                )
            )
        except RowCapExceededError as exc:
            issues.append(
                (
                    None,
                    None,
                    IssueCode.TOO_MANY_ROWS,
                    f"This file has more than {exc.max_rows:,} rows. Please split it into smaller exports.",
                )
            )
        return None

    def _match_columns(
        self, header: list[str], uploaded_file: UploadedFile, issues: list[_Issue]
    ) -> dict[str, int] | None:
        schema = schema_for(uploaded_file.source_type)
        positions: dict[str, int] = {}
        missing = []
        for col in schema:
            idx = next((i for i, h in enumerate(header) if col.matches_header(h)), None)
            if idx is None:
                if col.is_required:
                    missing.append(col)
                # else: optional and absent - fine, just not in positions
            else:
                positions[col.field_name] = idx

        if missing:
            for col in missing:
                issues.append(
                    (
                        None,
                        col.field_name,
                        IssueCode.MISSING_COLUMN,
                        f"Missing required column: {col.display_name}.",
                    )
                )
            return None
        return positions

    def _check_rows(
        self,
        rows_iter,
        column_positions: dict[str, int],
        uploaded_file: UploadedFile,
        issues: list[_Issue],
    ) -> int:
        schema = schema_for(uploaded_file.source_type)
        row_count = 0
        try:
            for row_number, row in enumerate(rows_iter, start=2):  # header was row 1
                row_count += 1
                for col in schema:
                    idx = column_positions.get(col.field_name)
                    if idx is None:
                        continue  # optional column, absent from this file
                    value = row[idx] if idx < len(row) else ""
                    if not col.validator(value):
                        if len(issues) < self._max_issues:
                            issues.append(
                                (
                                    row_number,
                                    col.field_name,
                                    col.issue_code,
                                    f"Row {row_number}: '{col.display_name}' value {value!r} is invalid.",
                                )
                            )
        except RowCapExceededError as exc:
            issues.append(
                (
                    None,
                    None,
                    IssueCode.TOO_MANY_ROWS,
                    f"This file has more than {exc.max_rows:,} rows. Please split it into smaller exports.",
                )
            )
        return row_count

    def _finish(
        self,
        uploaded_file: UploadedFile,
        status: ValidationStatus,
        row_count: int,
        issues: list[_Issue],
        requested_by: str,
    ) -> FileValidation:
        record = FileValidation(
            uploaded_file_id=uploaded_file.id, status=status, row_count=row_count
        )
        for row_number, column_name, issue_code, message in issues:
            record.issues.append(
                ValidationIssue(
                    row_number=row_number,
                    column_name=column_name,
                    issue_code=issue_code,
                    message=message,
                )
            )
        self._db.add(record)
        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            raise ValidationPersistError(
                "We couldn't save the validation result. Please try again."
            ) from exc
        self._db.refresh(record)

        self._audit_logger.log(
            event="file_validation_passed" if status == ValidationStatus.PASSED else "file_validation_failed",
            user_id=requested_by,
            analysis_id=uploaded_file.analysis_id,
            metadata={
                "uploaded_file_id": uploaded_file.id,
                "issue_count": len(issues),
                "row_count": row_count,
            },
        )
        return record
