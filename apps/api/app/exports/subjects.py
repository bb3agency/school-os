"""What exports hold about one person (R3-1A; FR-DPDP-009, PRV-031).

- A student: each export freezes the ids of the students it covered (``ops.exports.student_ids``,
  FR-EXP-003), so the report counts the exports that included the student by kind and profile,
  with the latest date. Never the files, their object keys or download links.
- A staff member: the exports they asked for (when, kind, formats, how many students, status).
  The students named in those exports are other people and are not listed.

Queries use this module's own models only (bound parameters; RLS limits them to the school).
"""

from __future__ import annotations

import uuid

from sqlalchemy import any_, func, literal, select
from sqlalchemy.orm import Session

from app.core.report_format import cap_rows, fmt_count, fmt_date, humanise
from app.core.subjects import MAX_SECTION_ROWS, ReportSection, Subject
from app.exports.models import Export

CLASS_KEY = "exports"


def _student_sections(session: Session, student_id: uuid.UUID) -> list[ReportSection]:
    includes = literal(student_id) == any_(Export.student_ids)
    grouped = (
        select(
            Export.kind,
            Export.profile_key,
            func.count().label("n"),
            func.max(Export.created_at).label("latest"),
        )
        .where(includes)
        .group_by(Export.kind, Export.profile_key)
        .order_by(func.max(Export.created_at).desc(), Export.kind, Export.profile_key)
    )
    found = list(session.execute(grouped.limit(MAX_SECTION_ROWS + 1)))
    if not found:
        return []
    total = (
        len(found)
        if len(found) <= MAX_SECTION_ROWS
        else int(session.execute(select(func.count()).select_from(grouped.subquery())).scalar_one())
    )
    kept, note = cap_rows(
        [
            (humanise(kind), humanise(profile), fmt_count(int(n)), fmt_date(latest))
            for kind, profile, n, latest in found
        ],
        total,
    )
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Exports and printed reports that included this student",
            columns=("Kind of export", "Sheet or form", "Number of exports", "Latest on"),
            rows=tuple(kept),
            note=note,
        )
    ]


def _member_sections(session: Session, membership_id: uuid.UUID) -> list[ReportSection]:
    mine = Export.requested_by_membership == membership_id
    total = int(session.execute(select(func.count()).select_from(Export).where(mine)).scalar_one())
    if total == 0:
        return []
    found = session.execute(
        select(Export)
        .where(mine)
        .order_by(Export.created_at.desc(), Export.id.desc())
        .limit(MAX_SECTION_ROWS)
    ).scalars()
    kept, note = cap_rows(
        [
            (
                fmt_date(e.created_at),
                humanise(e.kind),
                humanise(e.profile_key),
                ", ".join(f.upper() for f in e.formats) or "—",
                fmt_count(e.student_count),
                humanise(e.status),
                fmt_date(e.files_deleted_at),
            )
            for e in found
        ],
        total,
    )
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Exports and printed reports this staff member asked for",
            columns=(
                "Asked for on",
                "Kind of export",
                "Sheet or form",
                "Formats",
                "Students",
                "Status",
                "Files deleted on",
            ),
            rows=tuple(kept),
            note=note,
        )
    ]


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """Exports that included a student, or exports a staff member asked for; [] for guardians
    (exports never record guardians) and when there is nothing."""
    if subject.kind == "student":
        return _student_sections(session, subject.id)
    if subject.kind == "member":
        return _member_sections(session, subject.id)
    return []


__all__ = ["subject_report"]
