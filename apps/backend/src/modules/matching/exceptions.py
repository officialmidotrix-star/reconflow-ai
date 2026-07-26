from __future__ import annotations


class MatchingError_(Exception):
    error_code: str = "MATCHING_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InsufficientTransactionsError(MatchingError_):
    """Raised when one or both sides have zero transactions - matching
    can't meaningfully run at all, so this is a precondition failure (an
    exception), not a FAILED MatchingRun."""

    error_code = "INSUFFICIENT_TRANSACTIONS"
    http_status = 409


class MatchingPersistError(MatchingError_):
    error_code = "MATCHING_PERSIST_FAILED"
    http_status = 500
