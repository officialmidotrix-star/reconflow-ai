from __future__ import annotations


class ManualReviewError_(Exception):
    error_code: str = "MANUAL_REVIEW_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MatchNotFoundError(ManualReviewError_):
    error_code = "MATCH_NOT_FOUND"
    http_status = 404


class DiscrepancyNotFoundError(ManualReviewError_):
    error_code = "DISCREPANCY_NOT_FOUND"
    http_status = 404


class TransactionNotFoundError(ManualReviewError_):
    error_code = "TRANSACTION_NOT_FOUND"
    http_status = 404


class TransactionNotInAnalysisError(ManualReviewError_):
    error_code = "TRANSACTION_NOT_IN_ANALYSIS"
    http_status = 409


class WrongSourceTypeError(ManualReviewError_):
    """Raised if the transaction passed as the POS side isn't actually a
    POS_EXPORT transaction, or vice versa for the platform side - a manual
    pairing must reference two real transactions of the right kind, not
    two arbitrary rows."""

    error_code = "WRONG_SOURCE_TYPE"
    http_status = 400


class TransactionAlreadyMatchedError(ManualReviewError_):
    error_code = "TRANSACTION_ALREADY_MATCHED"
    http_status = 409


class ReviewPersistError(ManualReviewError_):
    error_code = "REVIEW_PERSIST_FAILED"
    http_status = 500
