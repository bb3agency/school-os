"""APAAR consent register in a data principal's access report (R3-1A; FR-DPDP-009, PRV-031;
ADR-0048 decision 6).

The student's own consent history: each decision with its date, who decided (relationship) and
when the office recorded it. The office's free-text note and the evidence document are left out
(the documents module reports documents; notes are staff free text). Not an eraser: the register
is evidence of the parent's decision (``refused_legal_obligation``).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.apaar import repository as repo
from app.core.report_format import cap_rows, fmt_date, fmt_text, humanise
from app.core.subjects import ReportSection, Subject

CLASS_KEY = "apaar_consents"
_LANGUAGES = {"en": "English", "te": "Telugu"}


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The student's APAAR consent decisions, newest first ([] for other kinds or none)."""
    if subject.kind != "student":
        return []
    history = repo.history(session, subject.id)
    if not history:
        return []
    rows = [
        (
            humanise(row.status),
            fmt_date(row.decided_on),
            humanise(row.relationship),
            fmt_text(_LANGUAGES.get(row.form_language or "")),
            fmt_date(row.recorded_at),
        )
        for row in history
    ]
    kept, note = cap_rows(rows)
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="APAAR consent decisions",
            columns=("Decision", "Decided on", "Decided by", "Form language", "Recorded on"),
            rows=tuple(kept),
            note=note,
        )
    ]


__all__ = ["subject_report"]
