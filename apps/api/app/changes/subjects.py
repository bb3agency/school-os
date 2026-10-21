"""Change requests in a data principal's access report (R3-1A; FR-DPDP-009, PRV-031; ADR-0048
decision 6).

- **Student:** every change request about the student's own details: the detail, its status,
  the value before and the value asked for (C3 values decrypted: they are the subject's own),
  and when it was requested and decided.
- **Guardian:** the requests that changed the guardian's own name on a linked child's record
  (``father_name`` for a father, ``mother_name`` for a mother); nothing else of the child.

Left out: the free-text reason and decision note (staff writing), who asked and who decided, the
evidence document (the documents module reports documents). Aadhaar as-printed values only as
"held". Not an eraser: the requests are the evidence of official changes
(``refused_official_record``).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Final

from sqlalchemy.orm import Session

from app.changes import repository as repo
from app.changes.models import ChangeRequest
from app.core.report_format import (
    AADHAAR_HELD,
    cap_rows,
    fmt_date,
    fmt_text,
    humanise,
)
from app.core.subjects import ReportSection, Subject
from app.students import service as students

CLASS_KEY: Final = "change_requests"
COLUMNS: Final = ("Detail", "Status", "Value before", "Value asked for", "Requested", "Decided")
LAST4_HELD: Final = "Last 4 digits held"
# The guardian's own name on a linked child's record, by relationship.
GUARDIAN_NAME_KEYS: Final[Mapping[str, str]] = {
    "father": "father_name",
    "mother": "mother_name",
}


def _value(session: Session, row: ChangeRequest, *, new: bool) -> str:
    if row.attribute_key == "aadhaar_last4":
        return LAST4_HELD
    if row.attribute_key.startswith("aadhaar_"):
        return AADHAAR_HELD
    if new and row.new_value_date is not None:
        return fmt_date(row.new_value_date)
    if not new and row.old_value_date is not None:
        return fmt_date(row.old_value_date)
    from app.changes import service as _svc  # noqa: PLC0415 - service re-exports this module

    if new:
        return fmt_text(_svc._plain_new(session, row))
    return fmt_text(_svc._plain_old(session, row))


def _section(session: Session, rows: Sequence[ChangeRequest], title: str) -> ReportSection:
    labels = {a.key: a.label_en for a in students.attribute_catalog(session)}
    out = [
        (
            labels.get(row.attribute_key) or humanise(row.attribute_key),
            humanise(row.status),
            _value(session, row, new=False),
            _value(session, row, new=True),
            fmt_date(row.requested_at),
            fmt_date(row.decided_at),
        )
        for row in rows
    ]
    kept, note = cap_rows(out)
    return ReportSection(
        class_key=CLASS_KEY, title=title, columns=COLUMNS, rows=tuple(kept), note=note
    )


def _guardian_requests(session: Session, guardian_id: uuid.UUID) -> list[ChangeRequest]:
    child_ids = students.guardian_student_ids(session, guardian_id)
    keys_by_child: dict[uuid.UUID, str] = {}
    for link in students.guardians_of_students(session, child_ids):
        key = GUARDIAN_NAME_KEYS.get(link.relationship)
        if link.guardian_id == guardian_id and key is not None:
            keys_by_child[link.student_id] = key
    rows = repo.requests_about(session, keys_by_child, keys=set(keys_by_child.values()))
    return [r for r in rows if keys_by_child.get(r.student_id) == r.attribute_key]


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """Change requests about the subject's own details ([] for members or none)."""
    if subject.kind == "student":
        rows = list(repo.requests_about(session, [subject.id]))
        title = "Change requests for the student's official details"
    elif subject.kind == "guardian":
        rows = _guardian_requests(session, subject.id)
        title = "Change requests for the parent's name on the child's record"
    else:
        return []
    if not rows:
        return []
    return [_section(session, rows, title)]


__all__ = ["subject_report"]
