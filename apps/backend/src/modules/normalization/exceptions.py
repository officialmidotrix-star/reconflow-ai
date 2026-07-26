"""
Exceptions for the Data Normalization module.

Every failure path here is an exception, not a data outcome - unlike Data
Validation, where finding bad data is the expected/normal case being
screened for. By the time this module runs, the precondition (a PASSED
FileValidation) already guarantees the data is well-formed; anything that
still goes wrong here is exceptional, not routine.
"""

from __future__ import annotations


class NormalizationError_(Exception):
    error_code: str = "NORMALIZATION_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UploadedFileNotFoundError(NormalizationError_):
    error_code = "UPLOADED_FILE_NOT_FOUND"
    http_status = 404


class FileNotValidatedError(NormalizationError_):
    """Raised when there is no PASSED FileValidation for this uploaded
    file yet - normalization's precondition."""

    error_code = "FILE_NOT_VALIDATED"
    http_status = 409


class FileReadError(NormalizationError_):
    error_code = "FILE_READ_FAILED"
    http_status = 500


class BranchConfigurationMissingError(NormalizationError_):
    """Raised when the analysis's branch has no timezone/currency
    configured yet - a real precondition until Organization & Branch
    Management exists for real."""

    error_code = "BRANCH_CONFIGURATION_MISSING"
    http_status = 409


class NormalizationInternalError(NormalizationError_):
    """Raised if a row fails to parse despite a PASSED FileValidation -
    an inconsistency the precondition was supposed to rule out, so this
    is treated as a hard stop rather than a silently-dropped row in a
    financial reconciliation tool."""

    error_code = "NORMALIZATION_INTERNAL_ERROR"
    http_status = 500


class NormalizationPersistError(NormalizationError_):
    error_code = "NORMALIZATION_PERSIST_FAILED"
    http_status = 500
