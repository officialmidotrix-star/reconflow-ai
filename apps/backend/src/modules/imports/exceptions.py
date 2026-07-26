"""
Exceptions for the Data Import module.

Every exception carries an `error_code` (machine-readable, for the frontend
to branch on if it ever needs to) and a `message` that is already
safe/plain-language to show a restaurant owner or accountant directly - no
translation step should be needed at the API layer.
"""

from __future__ import annotations


class ImportError_(Exception):
    """Base class for all Data Import failures. Named with a trailing
    underscore to avoid shadowing the built-in ImportError."""

    error_code: str = "IMPORT_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidFileTypeError(ImportError_):
    error_code = "INVALID_FILE_TYPE"
    http_status = 400


class FileTooLargeError(ImportError_):
    error_code = "FILE_TOO_LARGE"
    http_status = 413


class EmptyFileError(ImportError_):
    error_code = "EMPTY_FILE"
    http_status = 400


class DuplicateFileError(ImportError_):
    error_code = "DUPLICATE_FILE"
    http_status = 409


class AnalysisNotFoundError(ImportError_):
    error_code = "ANALYSIS_NOT_FOUND"
    http_status = 404


class AnalysisNotAcceptingUploadsError(ImportError_):
    error_code = "ANALYSIS_NOT_ACCEPTING_UPLOADS"
    http_status = 409


class UnauthorizedBranchAccessError(ImportError_):
    error_code = "UNAUTHORIZED_BRANCH_ACCESS"
    http_status = 403


class MalwareDetectedError(ImportError_):
    error_code = "MALWARE_DETECTED"
    http_status = 400


class StorageWriteError(ImportError_):
    error_code = "STORAGE_WRITE_FAILED"
    http_status = 500
