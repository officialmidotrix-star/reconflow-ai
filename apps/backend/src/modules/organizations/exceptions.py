from __future__ import annotations


class OrganizationError_(Exception):
    error_code: str = "ORGANIZATION_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OrganizationNotFoundError(OrganizationError_):
    error_code = "ORGANIZATION_NOT_FOUND"
    http_status = 404


class BranchNotFoundError(OrganizationError_):
    error_code = "BRANCH_NOT_FOUND"
    http_status = 404


class InvalidTimezoneError(OrganizationError_):
    error_code = "INVALID_TIMEZONE"
    http_status = 400


class OrganizationPersistError(OrganizationError_):
    error_code = "ORGANIZATION_PERSIST_FAILED"
    http_status = 500
