"""
HTTP layer for the Reporting & Export module.

Two endpoints: generate (creates a new report row + file) and download
(streams the decrypted bytes back with the right content type). Unlike
most prior modules, a download/read endpoint is included from the start
here - a report nobody can download isn't a useful module.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from .exceptions import ReportError_
from .schemas import GenerateReportRequest, ReportResponse
from .service import MEDIA_TYPES, ReportService

router = APIRouter(tags=["reporting"])


def get_report_service() -> ReportService:
    """Real wiring assembled at application start-up, same pattern as
    every other module's router."""
    raise NotImplementedError(
        "Wire up ReportService (db session, storage, audit_logger) at "
        "application start-up and override this dependency."
    )


def get_current_user_id() -> str:
    """Placeholder for the Identity & Access module's auth dependency."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post(
    "/analyses/{analysis_id}/reports", response_model=ReportResponse, status_code=201
)
async def generate_report(
    analysis_id: str,
    body: GenerateReportRequest,
    requested_by: str = Depends(get_current_user_id),
    service: ReportService = Depends(get_report_service),
) -> ReportResponse:
    try:
        report = service.generate_report(
            analysis_id=analysis_id, format=body.format, requested_by=requested_by
        )
    except ReportError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return ReportResponse.model_validate(report)


@router.get("/reports/{report_id}/download")
async def download_report(
    report_id: str,
    service: ReportService = Depends(get_report_service),
) -> Response:
    try:
        content, format_ = service.download_report(report_id=report_id)
    except ReportError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc

    extension = "csv" if format_.value == "CSV" else "xlsx"
    return Response(
        content=content,
        media_type=MEDIA_TYPES[format_],
        headers={"Content-Disposition": f'attachment; filename="report.{extension}"'},
    )
