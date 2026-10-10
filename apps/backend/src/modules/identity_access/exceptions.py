from __future__ import annotations


class IdentityAccessError_(Exception):
    error_code: str = "IDENTITY_ACCESS_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EmailAlreadyExistsError(IdentityAccessError_):
    error_code = "EMAIL_ALREADY_EXISTS"
    http_status = 409


class InvalidCredentialsError(IdentityAccessError_):
    error_code = "INVALID_CREDENTIALS"
    http_status = 401


class InvalidSessionError(IdentityAccessError_):
    error_code = "INVALID_SESSION"
    http_status = 401


class UserNotFoundError(IdentityAccessError_):
    error_code = "USER_NOT_FOUND"
    http_status = 404


class InsufficientRoleError(IdentityAccessError_):
    error_code = "INSUFFICIENT_ROLE"
    http_status = 403


class IdentityPersistError(IdentityAccessError_):
    error_code = "IDENTITY_PERSIST_FAILED"
    http_status = 500


class InvalidUserScopeError(IdentityAccessError_):
    """Role/organization combination violates the tenancy invariants."""

    error_code = "INVALID_USER_SCOPE"
    http_status = 422


class OperatorRoleNotAssignableError(IdentityAccessError_):
    error_code = "OPERATOR_ROLE_NOT_ASSIGNABLE"
    http_status = 403


class CrossOrganizationError(IdentityAccessError_):
    """Deliberately the same shape as 'not found' - see access.py."""

    error_code = "NOT_FOUND"
    http_status = 404
