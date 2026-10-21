"""Documents in a data principal's access report and erasure plan (R3-1A; FR-DPDP-009,
FR-DPDP-013, PRV-031, PRV-032; ADR-0048 decisions 6 and 8).

- A student: the documents of the school linked to the student by another record (evidence
  for a value, a change request, a consent form, a certificate, ...; found through the
  database's foreign keys to ``kb.documents``, see ``repository.student_documents``), with
  title, type and upload date. Never the storage key, a URL, the hash or the file itself.
- A staff member: how many documents they uploaded and when the latest was.

Erasure never deletes documents by itself: a document linked to a student may be the evidence
for an official record (the database refuses to delete those anyway), and which of the others
are still needed is the school's decision. For a student the outcome is a manual step naming
how many documents to review (``count`` 0: nothing was erased); with no linked document it is
an outcome with count 0 and no step, so the plan still shows the class was checked. Members:
nothing (what they uploaded belongs to the school's records, not to them).
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy.orm import Session

from app.core.report_format import cap_rows, fmt_count, fmt_date, fmt_text, humanise
from app.core.subjects import MAX_SECTION_ROWS, ErasureOutcome, ReportSection, Subject
from app.documents import repository as repo
from app.identity import service as identity

CLASS_KEY = "documents"


def _student_section(session: Session, subject: Subject) -> list[ReportSection]:
    docs, total = repo.student_documents(session, subject.id, limit=MAX_SECTION_ROWS)
    if total == 0:
        return []
    rows = [(fmt_text(d.title), humanise(d.doc_type), fmt_date(d.created_at)) for d in docs]
    kept, note = cap_rows(rows, total)
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Documents linked to the student",
            columns=("Title", "Type", "Uploaded on"),
            rows=tuple(kept),
            note=note,
        )
    ]


def _member_section(session: Session, subject: Subject) -> list[ReportSection]:
    user_id = identity.user_id_for_membership(session, subject.id)
    if user_id is None:
        return []
    count, latest = repo.uploads_by(session, user_id)
    if count == 0:
        return []
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Documents uploaded",
            columns=("Detail", "Value"),
            rows=(
                ("Documents uploaded", fmt_count(count)),
                ("Latest upload on", fmt_date(latest)),
            ),
            note="The documents belong to the school's records; only the count is shown.",
        )
    ]


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """Documents linked to a student, or a member's upload count ([] for guardians or none)."""
    if subject.kind == "student":
        return _student_section(session, subject)
    if subject.kind == "member":
        return _member_section(session, subject)
    return []


def erase_for_subject(
    session: Session, subject: Subject, class_keys: Collection[str], *, dry_run: bool
) -> list[ErasureOutcome]:
    """Never deletes: for a student, a manual step to review the linked documents (the same
    answer with and without ``dry_run``)."""
    if subject.kind != "student" or CLASS_KEY not in class_keys:
        return []
    _, total = repo.student_documents(session, subject.id, limit=0)
    if total == 0:
        return [ErasureOutcome(CLASS_KEY, 0)]
    noun = "document" if total == 1 else "documents"
    return [
        ErasureOutcome(
            CLASS_KEY,
            0,
            manual_steps=(
                f"Review the {fmt_count(total)} {noun} linked to this student in Documents; "
                "delete those not needed as evidence for official records.",
            ),
        )
    ]


__all__ = ["erase_for_subject", "subject_report"]
