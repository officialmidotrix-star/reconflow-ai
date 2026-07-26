"""
Exceptions for the Data Validation module.

Note the distinction from Data Import: a file that turns out to contain
bad data is NOT an exception here - it's a successful validation run that
found problems, recorded as a FAILED FileValidation with ValidationIssues.
These exceptions are reserved for cases where the *request itself* can't
be carried out at all.
"""

from __future__ import annotations


class ValidationError_(Exception):
    error_code: str = "VALIDATION_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UploadedFileNotFoundError(ValidationError_):
    error_code = "UPLOADED_FILE_NOT_FOUND"
    http_status = 404


class FileReadError(ValidationError_):
    error_code = "FILE_READ_FAILED"
    http_status = 500


class ValidationPersistError(ValidationError_):
    error_code = "VALIDATION_PERSIST_FAILED"
    http_status = 500
