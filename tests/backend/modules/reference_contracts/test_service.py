"""
Unit tests for ReferenceContractService.

All cross-module imports live at module level (not deferred inside test
functions) so every needed table registers in the shared metadata before
the db fixture's create_all() runs - the exact bug Organization & Branch
Management's own capstone test hit, avoided here from the start.

The last test class mirrors every prior foundational module's own
integration test: it hands this module's real service straight to a real
ComparisonService.run_comparison() call, proving the ContractLookup shape
Financial Comparison declared as a stand-in is now satisfied for real.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from sqlalchemy import Column, String, Table, create_engine
from sqlalchemy.orm import Session

from modules.analysis_orchestration.models import Analysis, AnalysisStatus
from modules.financial_comparison.dependencies import InMemoryAuditLogger as ComparisonAuditLogger
from modules.financial_comparison.service import ComparisonService
from modules.imports.models import Base, SourceType
from modules.matching.models import ReconciliationMatch
from modules.normalization.models import Transaction
from modules.organizations.dependencies import InMemoryAuditLogger as OrgAuditLogger
from modules.organizations.service import OrganizationService
from modules.reference_contracts.dependencies import InMemoryAuditLogger
from modules.reference_contracts.exceptions import (
    BranchNotFoundError,
    OverlappingContractError,
    PlatformAlreadyExistsError,
    PlatformNotFoundError,
)
from modules.reference_contracts.service import ReferenceContractService

USER_ID = "user-1"

if "users" not in Base.metadata.tables:
    Table("users", Base.metadata, Column("id", String(36), primary_key=True))


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def audit_logger():
    return InMemoryAuditLogger()


@pytest.fixture()
def service(db, audit_logger):
    return ReferenceContractService(db=db, audit_logger=audit_logger)


def _make_branch(db) -> str:
    org_service = OrganizationService(db=db, audit_logger=OrgAuditLogger())
    org = org_service.create_organization(
        legal_name="Acme Restaurants", default_currency="SAR", requested_by=USER_ID
    )
    branch = org_service.create_branch(
        organization_id=org.id, name="Downtown", timezone="Asia/Riyadh", requested_by=USER_ID
    )
    return branch.id


def _make_analysis(db, branch_id: str) -> str:
    analysis = Analysis(
        branch_id=branch_id, created_by=USER_ID, version=1, status=AnalysisStatus.AWAITING_FILES,
        period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis.id


class TestCreatePlatform:
    def test_succeeds(self, service):
        platform = service.create_platform(name="Talabat", requested_by=USER_ID)
        assert platform.name == "Talabat"

    def test_duplicate_name_rejected(self, service):
        service.create_platform(name="Talabat", requested_by=USER_ID)
        with pytest.raises(PlatformAlreadyExistsError):
            service.create_platform(name="Talabat", requested_by=USER_ID)

    def test_list_platforms(self, service):
        service.create_platform(name="Talabat", requested_by=USER_ID)
        service.create_platform(name="Jahez", requested_by=USER_ID)
        names = {p.name for p in service.list_platforms()}
        assert names == {"Talabat", "Jahez"}


class TestCreateContract:
    def test_succeeds_under_valid_branch_and_platform(self, service, db):
        branch_id = _make_branch(db)
        platform = service.create_platform(name="Talabat", requested_by=USER_ID)
        contract = service.create_contract(
            branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.15"),
            valid_from=date(2026, 1, 1), valid_to=None, requested_by=USER_ID,
        )
        assert contract.commission_pct == Decimal("0.1500")

    def test_unknown_branch_rejected(self, service):
        platform = service.create_platform(name="Talabat", requested_by=USER_ID)
        with pytest.raises(BranchNotFoundError):
            service.create_contract(
                branch_id="does-not-exist", platform_id=platform.id, commission_pct=Decimal("0.15"),
                valid_from=date(2026, 1, 1), valid_to=None, requested_by=USER_ID,
            )

    def test_unknown_platform_rejected(self, service, db):
        branch_id = _make_branch(db)
        with pytest.raises(PlatformNotFoundError):
            service.create_contract(
                branch_id=branch_id, platform_id="does-not-exist", commission_pct=Decimal("0.15"),
                valid_from=date(2026, 1, 1), valid_to=None, requested_by=USER_ID,
            )

    def test_overlapping_date_range_rejected(self, service, db):
        branch_id = _make_branch(db)
        platform = service.create_platform(name="Talabat", requested_by=USER_ID)
        service.create_contract(
            branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.15"),
            valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 1), requested_by=USER_ID,
        )
        with pytest.raises(OverlappingContractError):
            service.create_contract(
                branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.18"),
                valid_from=date(2026, 2, 1), valid_to=None, requested_by=USER_ID,
            )

    def test_sequential_non_overlapping_contracts_allowed(self, service, db):
        branch_id = _make_branch(db)
        platform = service.create_platform(name="Talabat", requested_by=USER_ID)
        service.create_contract(
            branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.15"),
            valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 1), requested_by=USER_ID,
        )
        # Starts exactly where the previous one ends - not an overlap.
        second = service.create_contract(
            branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.18"),
            valid_from=date(2026, 6, 1), valid_to=None, requested_by=USER_ID,
        )
        assert second.commission_pct == Decimal("0.1800")

    def test_list_contracts_for_branch(self, service, db):
        branch_id = _make_branch(db)
        platform = service.create_platform(name="Talabat", requested_by=USER_ID)
        service.create_contract(
            branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.15"),
            valid_from=date(2026, 1, 1), valid_to=None, requested_by=USER_ID,
        )
        assert len(service.list_contracts_for_branch(branch_id=branch_id)) == 1


class TestGetCommissionRate:
    def test_resolves_active_contract(self, service, db):
        branch_id = _make_branch(db)
        analysis_id = _make_analysis(db, branch_id)
        platform = service.create_platform(name="Talabat", requested_by=USER_ID)
        service.create_contract(
            branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.15"),
            valid_from=date(2026, 1, 1), valid_to=None, requested_by=USER_ID,
        )
        rate = service.get_commission_rate(
            analysis_id=analysis_id, as_of=datetime(2026, 3, 1, tzinfo=dt_timezone.utc)
        )
        assert rate == Decimal("0.1500")

    def test_returns_none_with_zero_contracts(self, service, db):
        branch_id = _make_branch(db)
        analysis_id = _make_analysis(db, branch_id)
        rate = service.get_commission_rate(
            analysis_id=analysis_id, as_of=datetime(2026, 3, 1, tzinfo=dt_timezone.utc)
        )
        assert rate is None

    def test_returns_none_when_ambiguous(self, service, db):
        branch_id = _make_branch(db)
        analysis_id = _make_analysis(db, branch_id)
        talabat = service.create_platform(name="Talabat", requested_by=USER_ID)
        jahez = service.create_platform(name="Jahez", requested_by=USER_ID)
        service.create_contract(
            branch_id=branch_id, platform_id=talabat.id, commission_pct=Decimal("0.15"),
            valid_from=date(2026, 1, 1), valid_to=None, requested_by=USER_ID,
        )
        service.create_contract(
            branch_id=branch_id, platform_id=jahez.id, commission_pct=Decimal("0.12"),
            valid_from=date(2026, 1, 1), valid_to=None, requested_by=USER_ID,
        )
        rate = service.get_commission_rate(
            analysis_id=analysis_id, as_of=datetime(2026, 3, 1, tzinfo=dt_timezone.utc)
        )
        assert rate is None  # two concurrent contracts - can't disambiguate, not a crash

    def test_temporal_correctness(self, service, db):
        branch_id = _make_branch(db)
        analysis_id = _make_analysis(db, branch_id)
        platform = service.create_platform(name="Talabat", requested_by=USER_ID)
        service.create_contract(
            branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.10"),
            valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 1), requested_by=USER_ID,
        )
        service.create_contract(
            branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.15"),
            valid_from=date(2026, 6, 1), valid_to=None, requested_by=USER_ID,
        )
        old_rate = service.get_commission_rate(
            analysis_id=analysis_id, as_of=datetime(2026, 3, 1, tzinfo=dt_timezone.utc)
        )
        new_rate = service.get_commission_rate(
            analysis_id=analysis_id, as_of=datetime(2026, 7, 1, tzinfo=dt_timezone.utc)
        )
        assert old_rate == Decimal("0.1000")
        assert new_rate == Decimal("0.1500")

    def test_unknown_analysis_returns_none(self, service):
        assert service.get_commission_rate(
            analysis_id="does-not-exist", as_of=datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
        ) is None


class TestAuditLogging:
    def test_records_platform_and_contract_creation(self, service, db, audit_logger):
        branch_id = _make_branch(db)
        platform = service.create_platform(name="Talabat", requested_by=USER_ID)
        assert audit_logger.records[-1].event == "platform_created"
        service.create_contract(
            branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.15"),
            valid_from=date(2026, 1, 1), valid_to=None, requested_by=USER_ID,
        )
        assert audit_logger.records[-1].event == "contract_created"


class TestSatisfiesFinancialComparisonContractLookup:
    def test_real_service_works_with_a_real_comparison_run(self, db, service):
        branch_id = _make_branch(db)
        analysis_id = _make_analysis(db, branch_id)
        platform = service.create_platform(name="Talabat", requested_by=USER_ID)
        service.create_contract(
            branch_id=branch_id, platform_id=platform.id, commission_pct=Decimal("0.15"),
            valid_from=date(2026, 1, 1), valid_to=None, requested_by=USER_ID,
        )

        occurred_at = datetime(2026, 3, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
        pos_txn = Transaction(
            analysis_id=analysis_id, uploaded_file_id="uploaded-file-pos",
            source_type=SourceType.POS_EXPORT, external_reference="1001",
            occurred_at=occurred_at, amount=Decimal("100.00"), currency_code="SAR",
        )
        platform_txn = Transaction(
            analysis_id=analysis_id, uploaded_file_id="uploaded-file-platform",
            source_type=SourceType.PLATFORM_SETTLEMENT, external_reference="1001",
            occurred_at=occurred_at, amount=Decimal("100.00"), currency_code="SAR",
            platform_commission_amount=Decimal("15.00"),
        )
        db.add_all([pos_txn, platform_txn])
        db.commit()
        db.refresh(pos_txn)
        db.refresh(platform_txn)

        match = ReconciliationMatch(
            analysis_id=analysis_id, pos_transaction_id=pos_txn.id,
            platform_transaction_id=platform_txn.id, confidence_score=Decimal("1.00"),
        )
        db.add(match)
        db.commit()

        comparison_service = ComparisonService(
            db=db, contract_lookup=service, audit_logger=ComparisonAuditLogger()
        )
        run = comparison_service.run_comparison(analysis_id=analysis_id, requested_by=USER_ID)

        assert run.compared_count == 1
        assert run.within_tolerance_count == 1  # commission matches the real configured rate exactly
