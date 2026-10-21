"""Consents for optional purposes in a data principal's access report (R3-1B; FR-DPDP-009,
FR-DPDP-025, PRV-031; ADR-0048 decision 6, ADR-0049).

The student's own decisions per purpose, newest first: the purpose as the school worded it, the
decision, the date on the form, who decided (relationship) and the form version. The office's
note and the evidence document are left out (the documents module reports documents; notes are
staff free text). Not an eraser: the register is evidence of the family's decision
(``refused_legal_obligation``); a withdrawal is recorded as a new decision instead.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.report_format import cap_rows, fmt_date, humanise
from app.core.subjects import ReportSection, Subject
from app.privacy import consents_repository as repo

CLASS_KEY = "consents"


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The student's decisions on the school's optional purposes ([] for other kinds or none)."""
    if subject.kind != "student":
        return []
    history = repo.history(session, subject.id)
    if not history:
        return []
    labels = {p.id: p.label_en for p in repo.list_purposes(session)}
    history = sorted(history, key=lambda r: (labels.get(r.purpose_id, ""), -r.seq))
    rows = [
        (
            labels.get(row.purpose_id, "Purpose"),
            humanise(row.status),
            fmt_date(row.decided_on),
            humanise(row.relationship),
            str(row.form_version),
            fmt_date(row.recorded_at),
        )
        for row in history
    ]
    kept, note = cap_rows(rows)
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Consents for optional purposes",
            columns=(
                "Purpose",
                "Decision",
                "Decided on",
                "Decided by",
                "Form version",
                "Recorded on",
            ),
            rows=tuple(kept),
            note=note,
        )
    ]


__all__ = ["subject_report"]
