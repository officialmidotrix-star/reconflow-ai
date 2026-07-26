from __future__ import annotations


class AuditLoggingError_(Exception):
    """Minimal base, kept for consistency with every other module's
    exception hierarchy - log() itself never raises (see package
    docstring), so there's little for the API layer to translate beyond
    generic query failures."""

    error_code: str = "AUDIT_LOGGING_ERROR"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
