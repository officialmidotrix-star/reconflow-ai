"""
Core business logic for the Reporting & Export module.

`generate_report` requires the analysis to be COMPLETED - a report for an
in-flight or failed analysis doesn't have a stable story to tell yet.
CSV gets the flat discrepancy list (naturally tabular); XLSX gets a
richer two-sheet workbook (Summary + Discrepancies), since a single flat
format can't hold both aggregate context and detail rows well.
"""

from __future__ import annotations

import csv
import io
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from modules.ai_insights.models import AIInsight
from modules.analysis_orchestration.models import Analysis, AnalysisStatus
from modules.discrepancies.models import Discrepancy, DiscrepancyRun
from storage.file_storage import FileStorage

from .dependencies import AuditLogger
from .exceptions import (
    AnalysisNotCompletedError,
    AnalysisNotFoundError,
    ReportGenerationError,
    ReportNotFoundError,
    ReportPersistError,
)
from .models import Report, ReportFormat

MEDIA_TYPES = {
    ReportFormat.CSV: "text/csv",
    ReportFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class ReportService:
    def __init__(self, *, db: Session, storage: FileStorage, audit_logger: AuditLogger) -> None:
        self._db = db
        self._storage = storage
        self._audit_logger = audit_logger

    def generate_report(
        self, *, analysis_id: str, format: ReportFormat, requested_by: str  # noqa: A002
    ) -> Report:
        analysis = self._db.get(Analysis, analysis_id)
        if analysis is None:
            raise AnalysisNotFoundError("We couldn't find that analysis.")
        if analysis.status != AnalysisStatus.COMPLETED:
            raise AnalysisNotCompletedError(
                "A report can only be generated once the analysis has completed."
            )

        discrepancies = self._db.execute(
            select(Discrepancy).where(Discrepancy.analysis_id == analysis_id)
        ).scalars().all()
        discrepancy_run = self._latest_discrepancy_run(analysis_id)
        ai_insight = self._latest_ai_insight(analysis_id)

        try:
            if format == ReportFormat.CSV:
                content = self._render_csv(discrepancies)
                extension = "csv"
            else:
                content = self._render_xlsx(analysis, discrepancies, discrepancy_run, ai_insight)
                extension = "xlsx"
        except Exception as exc:  # noqa: BLE001
            raise ReportGenerationError(
                "We couldn't generate the report. Please try again."
            ) from exc

        storage_path = f"{analysis_id}/reports/{uuid.uuid4()}.{extension}"
        try:
            self._storage.save(storage_path, content)
        except Exception as exc:  # noqa: BLE001
            raise ReportGenerationError(
                "We couldn't save the generated report. Please try again."
            ) from exc

        report = Report(analysis_id=analysis_id, format=format, storage_path=storage_path)
        self._db.add(report)
        try:
            self._db.commit()
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            self._storage.delete(storage_path)  # don't leave an orphaned file
            raise ReportPersistError(
                "We couldn't save the report record. Please try again."
            ) from exc
        self._db.refresh(report)

        self._audit_logger.log(
            event="report_generated",
            user_id=requested_by,
            analysis_id=analysis_id,
            metadata={"format": format.value, "report_id": report.id},
        )
        return report

    def download_report(self, *, report_id: str) -> tuple[bytes, ReportFormat]:
        report = self._db.get(Report, report_id)
        if report is None:
            raise ReportNotFoundError("We couldn't find that report.")
        content = self._storage.read(report.storage_path)
        return content, report.format

    # -- internal steps -------------------------------------------------

    def _latest_discrepancy_run(self, analysis_id: str) -> DiscrepancyRun | None:
        return self._db.execute(
            select(DiscrepancyRun)
            .where(DiscrepancyRun.analysis_id == analysis_id)
            .order_by(DiscrepancyRun.checked_at.desc())
        ).scalars().first()

    def _latest_ai_insight(self, analysis_id: str) -> AIInsight | None:
        return self._db.execute(
            select(AIInsight)
            .where(AIInsight.analysis_id == analysis_id)
            .order_by(AIInsight.generated_at.desc())
        ).scalars().first()

    def _render_csv(self, discrepancies: list[Discrepancy]) -> bytes:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["category", "severity", "estimated_loss", "reconciliation_match_id"])
        for d in discrepancies:
            writer.writerow(
                [d.category.value, d.severity.value, str(d.estimated_loss), d.reconciliation_match_id]
            )
        return buffer.getvalue().encode("utf-8")

    def _render_xlsx(
        self,
        analysis: Analysis,
        discrepancies: list[Discrepancy],
        discrepancy_run: DiscrepancyRun | None,
        ai_insight: AIInsight | None,
    ) -> bytes:
        import openpyxl

        workbook = openpyxl.Workbook()
        summary = workbook.active
        summary.title = "Summary"
        summary.append(["Analysis ID", analysis.id])
        summary.append(["Period", f"{analysis.period_start} to {analysis.period_end}"])
        if discrepancy_run is not None:
            summary.append(["Total discrepancies", discrepancy_run.total_count])
            summary.append(["Critical", discrepancy_run.critical_count])
            summary.append(["High", discrepancy_run.high_count])
            summary.append(["Medium", discrepancy_run.medium_count])
            summary.append(["Low", discrepancy_run.low_count])
        summary.append([])
        summary.append(["Executive summary"])
        summary.append([ai_insight.executive_summary if ai_insight else "No AI summary available."])

        detail = workbook.create_sheet("Discrepancies")
        detail.append(["category", "severity", "estimated_loss", "reconciliation_match_id"])
        for d in discrepancies:
            detail.append(
                [d.category.value, d.severity.value, str(d.estimated_loss), d.reconciliation_match_id]
            )

        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()
