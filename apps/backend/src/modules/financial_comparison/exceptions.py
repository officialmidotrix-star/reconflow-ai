from __future__ import annotations


class ComparisonError_(Exception):
    error_code: str = "COMPARISON_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ComparisonInternalError(ComparisonError_):
    """Raised if a platform settlement transaction has no commission
    amount despite Normalization guaranteeing one for PLATFORM_SETTLEMENT
    rows - an inconsistency, not an expected outcome, so this aborts the
    run rather than being silently skipped."""

    error_code = "COMPARISON_INTERNAL_ERROR"
    http_status = 500


class ComparisonPersistError(ComparisonError_):
    error_code = "COMPARISON_PERSIST_FAILED"
    http_status = 500
