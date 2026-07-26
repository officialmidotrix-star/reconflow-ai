from __future__ import annotations


class AnalysisOrchestrationError_(Exception):
    error_code: str = "ANALYSIS_ORCHESTRATION_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AnalysisNotFoundError(AnalysisOrchestrationError_):
    error_code = "ANALYSIS_NOT_FOUND"
    http_status = 404


class InvalidStatusTransitionError(AnalysisOrchestrationError_):
    error_code = "INVALID_STATUS_TRANSITION"
    http_status = 409


class AnalysisPersistError(AnalysisOrchestrationError_):
    error_code = "ANALYSIS_PERSIST_FAILED"
    http_status = 500
