from __future__ import annotations


class ReferenceContractError_(Exception):
    error_code: str = "REFERENCE_CONTRACT_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BranchNotFoundError(ReferenceContractError_):
    error_code = "BRANCH_NOT_FOUND"
    http_status = 404


class PlatformNotFoundError(ReferenceContractError_):
    error_code = "PLATFORM_NOT_FOUND"
    http_status = 404


class PlatformAlreadyExistsError(ReferenceContractError_):
    error_code = "PLATFORM_ALREADY_EXISTS"
    http_status = 409


class OverlappingContractError(ReferenceContractError_):
    """Two contracts for the same branch+platform can't have overlapping
    date ranges - the rate for any given date must be unambiguous by
    construction, independent of the separate platform-disambiguation
    gap noted in this module's docstring."""

    error_code = "OVERLAPPING_CONTRACT"
    http_status = 409


class ReferenceContractPersistError(ReferenceContractError_):
    error_code = "REFERENCE_CONTRACT_PERSIST_FAILED"
    http_status = 500
