"""What circulars and tasks hold about one staff member (R3-1A; FR-DPDP-009, PRV-031).

The tasks assigned to the member: title (school task text), where it came from, due date,
status and when it was finished or cancelled. Task details and circular text are written by
other staff and are not repeated; parent notices name no staff member.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.circulars import repository as repo
from app.core.report_format import cap_rows, fmt_date, fmt_text, humanise
from app.core.subjects import MAX_SECTION_ROWS, ReportSection, Subject

CLASS_KEY = "circulars_and_tasks"


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The tasks assigned to this staff member (newest first); [] for other subject kinds or
    when the member has no tasks."""
    if subject.kind != "member":
        return []
    tasks, total = repo.tasks_owned_by(session, subject.id, limit=MAX_SECTION_ROWS)
    if total == 0:
        return []
    kept, note = cap_rows(
        [
            (
                fmt_text(t.title),
                humanise(t.source),
                fmt_date(t.created_at),
                fmt_date(t.due_on),
                humanise(t.status),
                fmt_date(t.completed_at or t.cancelled_at),
            )
            for t in tasks
        ],
        total,
    )
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Tasks assigned to this staff member",
            columns=("Task", "From", "Assigned on", "Due on", "Status", "Done or cancelled on"),
            rows=tuple(kept),
            note=note,
        )
    ]


__all__ = ["subject_report"]
