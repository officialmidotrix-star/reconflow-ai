"""
Integration seam for the Data Validation module.

Only one cross-cutting dependency doesn't exist yet: Audit Logging. This
module declares its own local Protocol for it (rather than importing Data
Import's) - audit logging isn't owned by Data Import either, it just also
needed one. Any real AuditLogger implementation, once that module is
built, will structurally satisfy both.

Reading Data Import's own UploadedFile/FileStorage is a real dependency
now (see service.py) and is imported directly there, not stubbed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


class AuditLogger(Protocol):
    def log(
        self,
        *,
        event: str,
        user_id: str,
        analysis_id: str | None,
        metadata: dict,
    ) -> None: ...


@dataclass
class AuditRecord:
    event: str
    user_id: str
    analysis_id: str | None
    metadata: dict
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryAuditLogger:
    """Not production code - see dependencies.py in the imports module for
    the same pattern and rationale."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def log(self, *, event: str, user_id: str, analysis_id: str | None, metadata: dict) -> None:
        self.records.append(
            AuditRecord(event=event, user_id=user_id, analysis_id=analysis_id, metadata=metadata)
        )
