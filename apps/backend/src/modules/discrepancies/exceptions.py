from __future__ import annotations


class DiscrepancyError_(Exception):
    error_code: str = "DISCREPANCY_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DiscrepancyPersistError(DiscrepancyError_):
    error_code = "DISCREPANCY_PERSIST_FAILED"
    http_status = 500
