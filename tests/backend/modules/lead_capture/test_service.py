from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from modules.analysis_orchestration.models import Analysis, AnalysisStatus, Base
from modules.lead_capture.exceptions import AnalysisNotFoundError
from modules.lead_capture.schemas import RecordLeadRequest
from modules.lead_capture.service import LeadCaptureService
from modules.organizations.models import Branch, Organization  # noqa: F401 - registers tables

ANALYSIS_ID = "analysis-1"
BRANCH_ID = "branch-1"
USER_ID = "user-1"


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Analysis(
                id=ANALYSIS_ID, branch_id=BRANCH_ID, created_by=USER_ID, version=1,
                status=AnalysisStatus.AWAITING_FILES,
                period_start=date(2026, 7, 15), period_end=date(2026, 7, 18),
            )
        )
        session.commit()
        yield session


@pytest.fixture()
def service(db):
    return LeadCaptureService(db=db)


def _request(**overrides):
    defaults = {
        "restaurant_name": "Downtown Grill",
        "contact_email": "owner@downtowngrill.example",
        "whatsapp_number": "+966501234567",
    }
    defaults.update(overrides)
    return RecordLeadRequest(**defaults)


class TestRecordLead:
    def test_raises_when_analysis_does_not_exist(self, service):
        with pytest.raises(AnalysisNotFoundError):
            service.record_lead("no-such-analysis", _request())

    def test_creates_a_new_lead(self, service):
        result = service.record_lead(ANALYSIS_ID, _request())

        assert result.analysis_id == ANALYSIS_ID
        assert result.restaurant_name == "Downtown Grill"
        assert result.contact_email == "owner@downtowngrill.example"
        assert result.whatsapp_number == "+966501234567"

    def test_calling_it_twice_updates_rather_than_duplicates(self, db, service):
        service.record_lead(ANALYSIS_ID, _request(restaurant_name="Downtown Grill"))
        result = service.record_lead(ANALYSIS_ID, _request(restaurant_name="Downtown Grill (corrected)"))

        assert result.restaurant_name == "Downtown Grill (corrected)"

        from sqlalchemy import select
        from modules.lead_capture.models import AnalysisLead

        rows = db.execute(select(AnalysisLead).where(AnalysisLead.analysis_id == ANALYSIS_ID)).scalars().all()
        assert len(rows) == 1
