"""
Imports every module's models so all tables are registered on Base.metadata
- without importing the web app (routers, storage, AI provider...). Alembic
uses this; so can any script that only needs the schema.

tests/backend/test_migrations.py asserts this list equals the set of tables
the full application registers, so a new module's models can't be forgotten.
"""

from __future__ import annotations

import importlib

MODEL_MODULES = [
    "modules.ai_insights.models",
    "modules.analysis_orchestration.models",
    "modules.audit_logging.models",
    "modules.deployment.models",
    "modules.discrepancies.models",
    "modules.financial_comparison.models",
    "modules.identity_access.models",
    "modules.imports.models",
    "modules.lead_capture.models",
    "modules.manual_review.models",
    "modules.matching.models",
    "modules.normalization.models",
    "modules.notification.models",
    "modules.organizations.models",
    "modules.reference_contracts.models",
    "modules.reporting.models",
    "modules.validation.models",
]


def import_all_models() -> None:
    for name in MODEL_MODULES:
        importlib.import_module(name)
