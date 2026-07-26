from __future__ import annotations


class NotificationError_(Exception):
    error_code: str = "NOTIFICATION_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AnalysisNotFoundError(NotificationError_):
    error_code = "ANALYSIS_NOT_FOUND"
    http_status = 404


class NotificationPersistError(NotificationError_):
    error_code = "NOTIFICATION_PERSIST_FAILED"
    http_status = 500
