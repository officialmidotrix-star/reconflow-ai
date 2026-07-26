"""
Core business logic for the Data Import module.

`ImportService.import_file` is the entire module boiled down to one call:
given raw bytes and where they belong, either the file ends up safely
stored with a metadata row, or nothing is persisted at all and a specific,
plain-language exception is raised. There is no partial-success state.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .dependencies import UPLOAD_ACCEPTING_STATES, AnalysisLookup, AuditLogger, AuthContext
from .exceptions import (
    AnalysisNotAcceptingUploadsError,
    AnalysisNotFoundError,
    DuplicateFileError,
    EmptyFileError,
    FileTooLargeError,
    InvalidFileTypeError,
    MalwareDetectedError,
    StorageWriteError,
    UnauthorizedBranchAccessError,
)
from .models import FileStatus, SourceType, UploadedFile
from .security import (
    MalwareScanner,
    compute_checksum,
    file_extension,
    is_allowed_extension,
    sanitize_filename,
)
from .storage import FileStorage, build_storage_path

DEFAULT_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB - comfortably covers a
# 100k-row CSV/XLSX export while still catching obviously-wrong files early.


class ImportService:
    def __init__(
        self,
        *,
        db: Session,
        storage: FileStorage,
        analysis_lookup: AnalysisLookup,
        audit_logger: AuditLogger,
        scanner: MalwareScanner,
        max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
    ) -> None:
        self._db = db
        self._storage = storage
        self._analysis_lookup = analysis_lookup
        self._audit_logger = audit_logger
        self._scanner = scanner
        self._max_size_bytes = max_size_bytes

    def import_file(
        self,
        *,
        analysis_id: str,
        source_type: SourceType,
        original_filename: str,
        content: bytes,
        auth: AuthContext,
    ) -> UploadedFile:
        try:
            branch_id = self._check_analysis_accepts_uploads(analysis_id)
            self._check_branch_access(auth, branch_id)
            self._check_file_guardrails(original_filename, content)
            checksum = compute_checksum(content)
            self._check_not_duplicate(analysis_id, source_type, checksum)

            previous = self._get_current_file(analysis_id, source_type)
            next_version = (previous.version + 1) if previous else 1

            storage_path = build_storage_path(
                branch_id=branch_id,
                analysis_id=analysis_id,
                source_type=source_type.value,
                version=next_version,
                extension=file_extension(sanitize_filename(original_filename)),
            )

            self._write_to_storage(storage_path, content)

            if previous is not None:
                previous.status = FileStatus.SUPERSEDED
                from .models import _utcnow  # local import avoids polluting module namespace

                previous.superseded_at = _utcnow()

            record = UploadedFile(
                analysis_id=analysis_id,
                source_type=source_type,
                original_filename=original_filename,
                storage_path=storage_path,
                checksum_sha256=checksum,
                size_bytes=len(content),
                status=FileStatus.RECEIVED,
                version=next_version,
                uploaded_by=auth.user_id,
            )
            self._db.add(record)

            try:
                self._db.commit()
            except Exception as exc:  # noqa: BLE001 - deliberately broad, see below
                # The file is already on disk but the DB row failed to
                # commit. Roll back the storage write too, so we never end
                # up with an orphaned file that no metadata record points
                # to - "both succeed or both roll back."
                self._db.rollback()
                self._storage.delete(storage_path)
                raise StorageWriteError(
                    "We couldn't save your file. Please try uploading it again."
                ) from exc

            self._db.refresh(record)
            self._audit(
                event="file_import_succeeded",
                auth=auth,
                analysis_id=analysis_id,
                metadata={"file_id": record.id, "source_type": source_type.value},
            )
            return record

        except (
            AnalysisNotFoundError,
            AnalysisNotAcceptingUploadsError,
            UnauthorizedBranchAccessError,
            InvalidFileTypeError,
            FileTooLargeError,
            EmptyFileError,
            MalwareDetectedError,
            DuplicateFileError,
        ) as exc:
            self._audit(
                event="file_import_rejected",
                auth=auth,
                analysis_id=analysis_id,
                metadata={"error_code": exc.error_code, "filename": original_filename},
            )
            raise

    # -- internal steps, each one one clear failure reason ------------------

    def _check_analysis_accepts_uploads(self, analysis_id: str) -> str:
        status = self._analysis_lookup.get_status(analysis_id)
        if status is None:
            raise AnalysisNotFoundError("We couldn't find that analysis.")
        if status not in UPLOAD_ACCEPTING_STATES:
            raise AnalysisNotAcceptingUploadsError(
                "This analysis is no longer accepting file uploads."
            )
        branch_id = self._analysis_lookup.get_branch_id(analysis_id)
        assert branch_id is not None  # invariant: exists if status exists
        return branch_id

    def _check_branch_access(self, auth: AuthContext, branch_id: str) -> None:
        if not auth.can_access_branch(branch_id):
            raise UnauthorizedBranchAccessError(
                "You don't have access to upload files for this branch."
            )

    def _check_file_guardrails(self, original_filename: str, content: bytes) -> None:
        if not is_allowed_extension(original_filename):
            raise InvalidFileTypeError(
                "Please upload a .csv, .xlsx, or .xls file."
            )
        if len(content) == 0:
            raise EmptyFileError("That file appears to be empty. Please check and re-upload.")
        if len(content) > self._max_size_bytes:
            limit_mb = self._max_size_bytes // (1024 * 1024)
            raise FileTooLargeError(f"That file is larger than the {limit_mb}MB limit.")
        if not self._scanner.scan(content):
            raise MalwareDetectedError(
                "This file couldn't be accepted for security reasons. Please re-export and try again."
            )

    def _check_not_duplicate(
        self, analysis_id: str, source_type: SourceType, checksum: str
    ) -> None:
        existing = self._db.execute(
            select(UploadedFile).where(
                UploadedFile.analysis_id == analysis_id,
                UploadedFile.source_type == source_type,
                UploadedFile.checksum_sha256 == checksum,
                UploadedFile.status == FileStatus.RECEIVED,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicateFileError("This exact file has already been uploaded.")

    def _get_current_file(
        self, analysis_id: str, source_type: SourceType
    ) -> UploadedFile | None:
        return self._db.execute(
            select(UploadedFile).where(
                UploadedFile.analysis_id == analysis_id,
                UploadedFile.source_type == source_type,
                UploadedFile.status == FileStatus.RECEIVED,
            )
        ).scalar_one_or_none()

    def _write_to_storage(self, storage_path: str, content: bytes) -> None:
        try:
            self._storage.save(storage_path, content)
        except Exception as exc:  # noqa: BLE001
            raise StorageWriteError(
                "We couldn't save your file. Please try uploading it again."
            ) from exc

    def _audit(
        self, *, event: str, auth: AuthContext, analysis_id: str | None, metadata: dict
    ) -> None:
        self._audit_logger.log(
            event=event, user_id=auth.user_id, analysis_id=analysis_id, metadata=metadata
        )
