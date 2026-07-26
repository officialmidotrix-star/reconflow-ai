from __future__ import annotations


class AIInsightError_(Exception):
    error_code: str = "AI_INSIGHT_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AIProviderError(AIInsightError_):
    """Raised when the underlying AI provider call fails (network error,
    auth failure, rate limit, etc.) - an upstream failure, not ours."""

    error_code = "AI_PROVIDER_FAILED"
    http_status = 502


class GroundingViolationError(AIInsightError_):
    """Raised when the generated text contains a figure that wasn't in the
    computed facts it was given. The generated summary is rejected, not
    persisted, when this happens - the grounding contract is enforced,
    not just requested via the prompt."""

    error_code = "GROUNDING_VIOLATION"
    http_status = 500


class AIInsightPersistError(AIInsightError_):
    error_code = "AI_INSIGHT_PERSIST_FAILED"
    http_status = 500
