"""What the spreadsheet imports hold about one student (R3-1A; FR-DPDP-009, PRV-031).

Only the batches whose rows were matched to the student: when the batch was started and
committed, its source and status, and how many of its rows name the student. Never the raw cell
values, staged edits (ciphertext) or the uploaded file.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.report_format import cap_rows, fmt_count, fmt_date, humanise
from app.core.subjects import MAX_SECTION_ROWS, ReportSection, Subject
from app.imports import repository as repo

CLASS_KEY = "imports"


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The import batches that named this student (newest first); [] for other subject kinds
    or when no import row names the student."""
    if subject.kind != "student":
        return []
    found, total = repo.batches_for_student(session, subject.id, limit=MAX_SECTION_ROWS + 1)
    if not found:
        return []
    kept, note = cap_rows(
        [
            (
                fmt_date(batch.created_at),
                humanise(batch.source),
                humanise(batch.status),
                fmt_date(batch.committed_at),
                fmt_date(batch.reverted_at),
                fmt_count(rows),
            )
            for batch, rows in found
        ],
        total,
    )
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Spreadsheet imports that included this student",
            columns=(
                "Import started",
                "Source",
                "Status",
                "Committed on",
                "Undone on",
                "Rows about this student",
            ),
            rows=tuple(kept),
            note=note,
        )
    ]


__all__ = ["subject_report"]
