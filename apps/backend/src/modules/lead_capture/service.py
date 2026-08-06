"""
Core logic for Lead Capture.

record_lead() upserts rather than strictly-creates on purpose: Upload
calls this exactly once per submission in the intended flow, right after
getting analysis_id back from the n8n webhook, but a network retry on
that specific call shouldn't turn into a 409 over a duplicate - the
second call recording the same information again is harmless, and
"harmless to retry" is worth more here than "strictly one insert ever".
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.analysis_orchestration.models import Analysis

from .exceptions import AnalysisNotFoundError
from .models import AnalysisLead
from .schemas import LeadResponse, RecordLeadRequest


class LeadCaptureService:
    def __init__(self, *, db: Session) -> None:
        self._db = db

    def record_lead(self, analysis_id: str, body: RecordLeadRequest) -> LeadResponse:
        if self._db.get(Analysis, analysis_id) is None:
            raise AnalysisNotFoundError("We couldn't find that analysis.")

        existing = self._db.execute(
            select(AnalysisLead).where(AnalysisLead.analysis_id == analysis_id)
        ).scalar_one_or_none()

        if existing is not None:
            existing.restaurant_name = body.restaurant_name
            existing.contact_email = body.contact_email
            existing.whatsapp_number = body.whatsapp_number
            lead = existing
        else:
            lead = AnalysisLead(
                analysis_id=analysis_id,
                restaurant_name=body.restaurant_name,
                contact_email=body.contact_email,
                whatsapp_number=body.whatsapp_number,
            )
            self._db.add(lead)

        self._db.commit()
        self._db.refresh(lead)
        return LeadResponse.model_validate(lead)
