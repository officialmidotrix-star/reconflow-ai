from __future__ import annotations


class DeploymentError_(Exception):
    error_code: str = "DEPLOYMENT_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InsufficientPrivilegeError(DeploymentError_):
    """Recording a license or an update requires the Owner role - checked
    against Identity & Access's real User table, not a Protocol stub."""

    error_code = "INSUFFICIENT_PRIVILEGE"
    http_status = 403


class DeploymentPersistError(DeploymentError_):
    error_code = "DEPLOYMENT_PERSIST_FAILED"
    http_status = 500
