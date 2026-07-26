"""
HTTP layer for the Data Import module.

Deliberately thin: parse the request, call ImportService, translate the
result. No business logic lives here - see service.py for that.

This endpoint is called directly by the web app as part of the synchronous
"upload & validate" fast path from the Phase 2 data flow. n8n is never
involved in receiving a file - it only starts orchestrating once both
files exist and have passed validation, which is a different module's
concern (Analysis Orchestration).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .dependencies import AuthContext
from .exceptions import ImportError_
from .models import SourceType
from .schemas import UploadedFileResponse
from .service import ImportService

router = APIRouter(prefix="/analyses/{analysis_id}/files", tags=["data-import"])


def get_import_service() -> ImportService:
    """Real dependency wiring (DB session, storage backend, and the real
    Analysis Orchestration / Audit Logging implementations once they exist)
    is assembled at application start-up and overridden here via FastAPI's
    dependency-override mechanism. Left unimplemented in this module's own
    file on purpose - it's an application-level concern, not this module's."""
    raise NotImplementedError(
        "Wire up ImportService (db session, storage, analysis_lookup, "
        "audit_logger) at application start-up and override this dependency."
    )


def get_current_auth() -> AuthContext:
    """Placeholder for the Identity & Access module's auth dependency,
    which will supply the real authenticated AuthContext once that module
    is implemented in its own Phase 3 turn."""
    raise NotImplementedError("Wire up real authentication in the Identity & Access module.")


@router.post("", response_model=UploadedFileResponse, status_code=201)
async def upload_file(
    analysis_id: str,
    source_type: SourceType = Form(...),
    file: UploadFile = File(...),
    auth: AuthContext = Depends(get_current_auth),
    service: ImportService = Depends(get_import_service),
) -> UploadedFileResponse:
    content = await file.read()
    try:
        record = service.import_file(
            analysis_id=analysis_id,
            source_type=source_type,
            original_filename=file.filename or "upload",
            content=content,
            auth=auth,
        )
    except ImportError_ as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"error_code": exc.error_code, "message": exc.message},
        ) from exc

    return UploadedFileResponse.model_validate(record)
