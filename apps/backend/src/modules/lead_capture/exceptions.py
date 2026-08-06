from __future__ import annotations


class LeadCaptureError_(Exception):
    error_code: str = "LEAD_CAPTURE_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AnalysisNotFoundError(LeadCaptureError_):
    error_code = "ANALYSIS_NOT_FOUND"
    http_status = 404
