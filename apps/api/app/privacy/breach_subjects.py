"""The breach register in a data principal's access report (R3-1B; FR-DPDP-009, PRV-031;
ADR-0048 decision 6, ADR-0050).

Breaches whose affected list names the subject: when the school noticed it, the kinds of data,
how many people were affected (approximate counts only, never who else), the status and when
families were told. The description and containment steps are left out (staff free text that
may name other people). Not an eraser: breach records are kept until their retention ends
(``allowed_after_retention``).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.report_format import cap_rows, fmt_count, fmt_date, humanise
from app.core.subjects import ReportSection, Subject
from app.privacy import breach_repository as repo
from app.privacy.breach_config import load_config

CLASS_KEY = "breach_records"


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """Breaches that list the student, guardian or staff member as affected ([] if none)."""
    rows_db = repo.breaches_affecting(
        session,
        student_id=subject.id if subject.kind == "student" else None,
        guardian_id=subject.id if subject.kind == "guardian" else None,
        membership_id=subject.id if subject.kind == "member" else None,
    )
    if not rows_db:
        return []
    labels = load_config().category_labels
    rows = [
        (
            fmt_date(r.noticed_at),
            ", ".join(labels.get(c, c) for c in r.categories),
            fmt_count(r.approx_children + r.approx_guardians + r.approx_staff),
            humanise(r.status),
            fmt_date(r.people_notified_at),
        )
        for r in rows_db
    ]
    kept, note = cap_rows(rows)
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Security incidents that affected this person's records",
            columns=(
                "Noticed on",
                "Kinds of data",
                "People affected (approx.)",
                "Status",
                "Families told on",
            ),
            rows=tuple(kept),
            note=note,
        )
    ]


__all__ = ["subject_report"]
