"""Data-quality checks in a data principal's access report (R3-1A; FR-DPDP-009, PRV-031;
ADR-0048 decision 6).

Counts only: findings about the student by check, severity and status, with the date of the
latest check. Finding details (masked values, resolution notes, the other student of a pair) are
working data and stay out. Not an eraser: checks follow the official record
(``refused_official_record``).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.report_format import cap_rows, fmt_count, fmt_date, humanise
from app.core.subjects import ReportSection, Subject
from app.dq import repository as repo
from app.dq.models import ACTIVE_STATUSES

CLASS_KEY = "data_quality"


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """Counts of the student's data-quality findings ([] for other kinds or none)."""
    if subject.kind != "student":
        return []
    counts = repo.subject_finding_counts(session, subject.id)
    if not counts:
        return []
    active = sum(int(r["n"]) for r in counts if r["status"] in ACTIVE_STATUSES)
    closed = sum(int(r["n"]) for r in counts) - active
    rows = [
        (
            str(r["rule_id"]),
            humanise(str(r["severity"])),
            humanise(str(r["status"])),
            fmt_count(int(r["n"])),
            fmt_date(r["last_seen_at"]),
        )
        for r in counts
    ]
    kept, note = cap_rows(rows)
    summary = f"Open findings: {fmt_count(active)}, closed: {fmt_count(closed)}."
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Data-quality checks",
            columns=("Check", "Severity", "Status", "Findings", "Last checked"),
            rows=tuple(kept),
            note=f"{summary} {note}" if note else summary,
        )
    ]


__all__ = ["subject_report"]
