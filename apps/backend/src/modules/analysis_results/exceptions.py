from __future__ import annotations


class AnalysisResultsError_(Exception):
    error_code: str = "ANALYSIS_RESULTS_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AnalysisNotFoundError(AnalysisResultsError_):
    error_code = "ANALYSIS_NOT_FOUND"
    http_status = 404
