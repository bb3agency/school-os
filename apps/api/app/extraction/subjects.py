"""What register-photo extraction holds about one student (R3-1A; FR-DPDP-009, PRV-031).

Only the photo batches with register rows confirmed for the student: when the photos were
uploaded, the batch status, how many pages it had, how many rows were confirmed for the student
and when. Never the images, object keys or what was read from them (the confirmed values
themselves are reported by the students module).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.report_format import cap_rows, fmt_count, fmt_date, humanise
from app.core.subjects import MAX_SECTION_ROWS, ReportSection, Subject
from app.extraction import repository as repo

CLASS_KEY = "register_photos"


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The register photo batches with rows confirmed for this student (newest first); [] for
    other subject kinds or when no row was linked to the student."""
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
                fmt_count(batch.page_count),
                fmt_count(items),
                fmt_date(reviewed),
            )
            for batch, items, reviewed in found
        ],
        total,
    )
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Register photos with rows about this student",
            columns=(
                "Photos uploaded",
                "Register",
                "Status",
                "Pages",
                "Rows about this student",
                "Last checked on",
            ),
            rows=tuple(kept),
            note=note,
        )
    ]


__all__ = ["subject_report"]
