from __future__ import annotations


class ReportError_(Exception):
    error_code: str = "REPORT_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AnalysisNotFoundError(ReportError_):
    error_code = "ANALYSIS_NOT_FOUND"
    http_status = 404


class AnalysisNotCompletedError(ReportError_):
    """A report is only generated from a completed analysis - one that's
    still in flight or failed doesn't have a stable, final story to tell."""

    error_code = "ANALYSIS_NOT_COMPLETED"
    http_status = 409


class ReportNotFoundError(ReportError_):
    error_code = "REPORT_NOT_FOUND"
    http_status = 404


class ReportGenerationError(ReportError_):
    error_code = "REPORT_GENERATION_FAILED"
    http_status = 500


class ReportPersistError(ReportError_):
    error_code = "REPORT_PERSIST_FAILED"
    http_status = 500
