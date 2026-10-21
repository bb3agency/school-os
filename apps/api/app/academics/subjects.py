"""Data-principal access report contribution of the academics module (R3-1A; FR-DPDP-009,
ADR-0048 decision 6, PRV-031).

A student's attendance as monthly counts per status and their marks per exam and subject (or
"Absent"). SchoolOS stores no grade, so none is shown. Attendance registers and marks are school
records (``refused_official_record``): this module never erases.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal
from typing import Final

from sqlalchemy.orm import Session

from app.academics import repository as repo
from app.core.report_format import cap_rows, fmt_count, fmt_date, fmt_text
from app.core.subjects import MAX_SECTION_ROWS, ReportSection, Subject

CLASS_KEY: Final = "attendance_and_marks"
STATUSES: Final = ("present", "late", "absent", "leave")
ATTENDANCE_COLUMNS: Final = ("Month", "Present", "Late", "Absent", "On leave")
MARKS_COLUMNS: Final = ("Exam", "Held on", "Subject", "Marks", "Maximum marks")


def _number(value: Decimal | None) -> str:
    """``45.00`` -> ``45``, ``45.50`` -> ``45.5``."""
    if value is None:
        return fmt_text(None)
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _attendance(session: Session, subject: Subject) -> ReportSection | None:
    months: dict[dt.date, dict[str, int]] = defaultdict(dict)
    for month, status, count in repo.attendance_by_month(session, subject.id):
        months[month][status] = count
    if not months:
        return None
    rows = [
        (month.strftime("%B %Y"), *(fmt_count(months[month].get(s, 0)) for s in STATUSES))
        for month in sorted(months, reverse=True)
    ]
    kept, note = cap_rows(rows)
    return ReportSection(
        class_key=CLASS_KEY,
        title="Attendance by month",
        columns=ATTENDANCE_COLUMNS,
        rows=tuple(kept),
        note=note,
    )


def _marks(session: Session, subject: Subject) -> ReportSection | None:
    found, total = repo.marks_of_student(session, subject.id, limit=MAX_SECTION_ROWS)
    if total == 0:
        return None
    rows = [
        (
            fmt_text(exam.name),
            fmt_date(exam.held_on),
            fmt_text(mark.subject),
            "Absent" if mark.absent else _number(mark.marks),
            _number(mark.max_marks),
        )
        for mark, exam in found
    ]
    kept, note = cap_rows(rows, total)
    return ReportSection(
        class_key=CLASS_KEY,
        title="Exam marks",
        columns=MARKS_COLUMNS,
        rows=tuple(kept),
        note=note,
    )


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The student's monthly attendance and exam marks, newest first; [] for other subject
    kinds or when nothing is recorded."""
    if subject.kind != "student":
        return []
    sections = [_attendance(session, subject), _marks(session, subject)]
    return [s for s in sections if s is not None]


__all__ = ["subject_report"]
