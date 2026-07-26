"""
Dependency wiring for all 16 modules.

Every module's router.py has a get_X_service() placeholder ("Real wiring
assembled at application start-up... override this dependency") and
(except Identity & Access, which owns the real one) a get_current_user_id
placeholder. This file provides the real implementations and registers
them via FastAPI's dependency_overrides mechanism - the exact mechanism
every router's docstring already pointed to.

Three of the real implementations here are the payoff of integration
tests written much earlier in this build, proving these exact
substitutions work before this file ever existed:
  - AnalysisOrchestrationService satisfies Data Import's AnalysisLookup
  - OrganizationService satisfies Normalization's AnalysisTimezoneLookup
  - ReferenceContractService satisfies Financial Comparison's ContractLookup
AuditLogService satisfies all 16 modules' identical AuditLogger protocol
at once, per Audit Logging's own design.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from storage.file_storage import LocalEncryptedFileStorage

from .config import get_encryption_key, get_storage_dir, has_anthropic_credentials, has_smtp_credentials
from .database import get_db

# Routers - importing each transitively imports its models (registering
# every table on Base.metadata) and its service/exceptions/dependencies.
from modules.ai_insights import router as ai_insights_router
from modules.analysis_orchestration import router as analysis_orchestration_router
from modules.audit_logging import router as audit_logging_router
from modules.deployment import router as deployment_router
from modules.discrepancies import router as discrepancies_router
from modules.financial_comparison import router as financial_comparison_router
from modules.identity_access import router as identity_access_router
from modules.imports import router as imports_router
from modules.manual_review import router as manual_review_router
from modules.matching import router as matching_router
from modules.normalization import router as normalization_router
from modules.notification import router as notification_router
from modules.organizations import router as organizations_router
from modules.reference_contracts import router as reference_contracts_router
from modules.reporting import router as reporting_router
from modules.validation import router as validation_router

# Services
from modules.ai_insights.dependencies import FakeAIProvider
from modules.ai_insights.providers.anthropic_provider import AnthropicAIProvider
from modules.ai_insights.service import AIInsightService
from modules.analysis_orchestration.service import AnalysisOrchestrationService
from modules.audit_logging.service import AuditLogService
from modules.deployment.service import DeploymentService
from modules.discrepancies.service import DiscrepancyService
from modules.financial_comparison.service import ComparisonService
from modules.identity_access.exceptions import IdentityAccessError_
from modules.identity_access.service import IdentityAccessService
from modules.imports.dependencies import AuthContext
from modules.imports.security import NoOpMalwareScanner
from modules.imports.service import ImportService
from modules.manual_review.service import ManualReviewService
from modules.matching.service import MatchingService
from modules.normalization.service import NormalizationService
from modules.notification.channels.email_channel import SMTPEmailChannel
from modules.notification.dependencies import FakeNotificationChannel
from modules.notification.service import NotificationService
from modules.organizations.service import OrganizationService
from modules.reference_contracts.service import ReferenceContractService
from modules.reporting.service import ReportService
from modules.validation.service import ValidationService

logger = logging.getLogger("reconflow.bootstrap.wiring")

_bearer_scheme = HTTPBearer(auto_error=False)

# -- singletons that don't need a per-request db session ---------------

_storage = LocalEncryptedFileStorage(get_storage_dir(), get_encryption_key())

if has_anthropic_credentials():
    _ai_provider = AnthropicAIProvider()
else:
    logger.warning(
        "ANTHROPIC_API_KEY is not set - using FakeAIProvider. AI-generated "
        "executive summaries will be templated placeholders, not real AI output."
    )
    _ai_provider = FakeAIProvider()

if has_smtp_credentials():
    _notification_channel = SMTPEmailChannel()
else:
    logger.warning(
        "SMTP_HOST/SMTP_FROM_ADDRESS are not set - using FakeNotificationChannel. "
        "Notifications will be recorded but not actually delivered."
    )
    _notification_channel = FakeNotificationChannel()


# -- per-request helper services (share the request's db session) -------


def _audit_logger(db: Session) -> AuditLogService:
    return AuditLogService(db=db)


def _identity_service(db: Session) -> IdentityAccessService:
    return IdentityAccessService(db=db, audit_logger=_audit_logger(db))


def _analysis_lookup(db: Session) -> AnalysisOrchestrationService:
    return AnalysisOrchestrationService(db=db, audit_logger=_audit_logger(db))


def _tz_lookup(db: Session) -> OrganizationService:
    return OrganizationService(db=db, audit_logger=_audit_logger(db))


def _contract_lookup(db: Session) -> ReferenceContractService:
    return ReferenceContractService(db=db, audit_logger=_audit_logger(db))


# -- shared auth resolvers, overriding every module's placeholder --------


def _current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> str:
    if credentials is None:
        raise HTTPException(
            status_code=401, detail={"error_code": "MISSING_TOKEN", "message": "Sign in required."}
        )
    try:
        user = _identity_service(db).get_current_user(token=credentials.credentials)
    except IdentityAccessError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc
    return user.id


def _current_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthContext:
    if credentials is None:
        raise HTTPException(
            status_code=401, detail={"error_code": "MISSING_TOKEN", "message": "Sign in required."}
        )
    try:
        return _identity_service(db).build_auth_context(token=credentials.credentials)
    except IdentityAccessError_ as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"error_code": exc.error_code, "message": exc.message}
        ) from exc


# -- per-module service factories, overriding each get_X_service --------


def _import_service(db: Session = Depends(get_db)) -> ImportService:
    return ImportService(
        db=db, storage=_storage, analysis_lookup=_analysis_lookup(db),
        audit_logger=_audit_logger(db), scanner=NoOpMalwareScanner(),
    )


def _validation_service(db: Session = Depends(get_db)) -> ValidationService:
    return ValidationService(db=db, storage=_storage, audit_logger=_audit_logger(db))


def _normalization_service(db: Session = Depends(get_db)) -> NormalizationService:
    return NormalizationService(
        db=db, storage=_storage, tz_lookup=_tz_lookup(db), audit_logger=_audit_logger(db)
    )


def _matching_service(db: Session = Depends(get_db)) -> MatchingService:
    return MatchingService(db=db, audit_logger=_audit_logger(db))


def _comparison_service(db: Session = Depends(get_db)) -> ComparisonService:
    return ComparisonService(db=db, contract_lookup=_contract_lookup(db), audit_logger=_audit_logger(db))


def _discrepancy_service(db: Session = Depends(get_db)) -> DiscrepancyService:
    return DiscrepancyService(db=db, audit_logger=_audit_logger(db))


def _manual_review_service(db: Session = Depends(get_db)) -> ManualReviewService:
    return ManualReviewService(db=db, audit_logger=_audit_logger(db))


def _ai_insight_service(db: Session = Depends(get_db)) -> AIInsightService:
    return AIInsightService(db=db, ai_provider=_ai_provider, audit_logger=_audit_logger(db))


def _orchestration_service(db: Session = Depends(get_db)) -> AnalysisOrchestrationService:
    return AnalysisOrchestrationService(db=db, audit_logger=_audit_logger(db))


def _report_service(db: Session = Depends(get_db)) -> ReportService:
    return ReportService(db=db, storage=_storage, audit_logger=_audit_logger(db))


def _notification_service(db: Session = Depends(get_db)) -> NotificationService:
    return NotificationService(db=db, channel=_notification_channel, audit_logger=_audit_logger(db))


def _identity_access_service(db: Session = Depends(get_db)) -> IdentityAccessService:
    return _identity_service(db)


def _organization_service(db: Session = Depends(get_db)) -> OrganizationService:
    return OrganizationService(db=db, audit_logger=_audit_logger(db))


def _reference_contract_service(db: Session = Depends(get_db)) -> ReferenceContractService:
    return ReferenceContractService(db=db, audit_logger=_audit_logger(db))


def _audit_log_service(db: Session = Depends(get_db)) -> AuditLogService:
    return AuditLogService(db=db)


def _deployment_service(db: Session = Depends(get_db)) -> DeploymentService:
    return DeploymentService(db=db, audit_logger=_audit_logger(db))


# -- registration ---------------------------------------------------------

_ALL_ROUTERS = [
    identity_access_router,
    organizations_router,
    reference_contracts_router,
    audit_logging_router,
    deployment_router,
    imports_router,
    validation_router,
    normalization_router,
    matching_router,
    financial_comparison_router,
    discrepancies_router,
    manual_review_router,
    ai_insights_router,
    analysis_orchestration_router,
    reporting_router,
    notification_router,
]

# Every module except Identity & Access (which owns the real
# get_current_user_id) uses the shared user-id resolver. Data Import is
# the one exception with a richer AuthContext instead.
_USER_ID_AUTH_MODULES = [
    validation_router, normalization_router, matching_router, financial_comparison_router,
    discrepancies_router, manual_review_router, ai_insights_router,
    analysis_orchestration_router, reporting_router, notification_router,
    organizations_router, reference_contracts_router, audit_logging_router, deployment_router,
]

_SERVICE_OVERRIDES = {
    imports_router.get_import_service: _import_service,
    validation_router.get_validation_service: _validation_service,
    normalization_router.get_normalization_service: _normalization_service,
    matching_router.get_matching_service: _matching_service,
    financial_comparison_router.get_comparison_service: _comparison_service,
    discrepancies_router.get_discrepancy_service: _discrepancy_service,
    manual_review_router.get_manual_review_service: _manual_review_service,
    ai_insights_router.get_ai_insight_service: _ai_insight_service,
    analysis_orchestration_router.get_orchestration_service: _orchestration_service,
    reporting_router.get_report_service: _report_service,
    notification_router.get_notification_service: _notification_service,
    identity_access_router.get_identity_access_service: _identity_access_service,
    organizations_router.get_organization_service: _organization_service,
    reference_contracts_router.get_reference_contract_service: _reference_contract_service,
    audit_logging_router.get_audit_log_service: _audit_log_service,
    deployment_router.get_deployment_service: _deployment_service,
}


def register_all(app: FastAPI) -> None:
    """Include every module's router and override every placeholder
    dependency with its real implementation. Called once, from main.py."""
    for router_module in _ALL_ROUTERS:
        app.include_router(router_module.router)

    for placeholder, real in _SERVICE_OVERRIDES.items():
        app.dependency_overrides[placeholder] = real

    for router_module in _USER_ID_AUTH_MODULES:
        app.dependency_overrides[router_module.get_current_user_id] = _current_user_id

    app.dependency_overrides[imports_router.get_current_auth] = _current_auth
