"""Database access for attendance, exams and marks (0035_student_insights).

Callers inside ``app.academics`` only. Every function runs in a ``core.db.tenant_session``: RLS
limits each statement to that school and ``tenant_id`` is taken from the session's context.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import func, literal_column, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.academics.models import AttendanceMark, Exam, ExamMark
from app.core.record_tables import dump_table
from app.core.records import RecordTable


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("academics repository used outside tenant_session")
    return uuid.UUID(str(value))


def _written(session: Session, stmt: Any) -> tuple[int, int]:
    """Run an upsert that RETURNs one row per inserted or changed row: (inserted, updated)."""
    rows = session.execute(stmt).all()
    inserted = sum(1 for r in rows if r.inserted)
    return inserted, len(rows) - inserted


# --- attendance -----------------------------------------------------------------------------------


def upsert_attendance(session: Session, rows: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Insert or correct marks (one per student and date). Unchanged rows are left alone.
    Returns (inserted, updated)."""
    if not rows:
        return 0, 0
    tenant_id = current_tenant_id(session)
    insert = pg_insert(AttendanceMark).values([{"tenant_id": tenant_id, **r} for r in rows])
    excluded = insert.excluded
    stmt: Any = insert.on_conflict_do_update(
        constraint="attendance_marks_one_per_day",
        set_={
            "status": excluded.status,
            "section_id": excluded.section_id,
            "source": excluded.source,
            "recorded_by": excluded.recorded_by,
            "version": AttendanceMark.version + 1,
        },
        where=or_(
            AttendanceMark.status != excluded.status,
            AttendanceMark.section_id != excluded.section_id,
        ),
    ).returning(AttendanceMark.id, literal_column("(xmax = 0)").label("inserted"))
    return _written(session, stmt)


def section_marks(
    session: Session, section_id: uuid.UUID, first: dt.date, last: dt.date
) -> list[AttendanceMark]:
    return list(
        session.scalars(
            select(AttendanceMark)
            .where(
                AttendanceMark.section_id == section_id,
                AttendanceMark.on_date >= first,
                AttendanceMark.on_date <= last,
            )
            .order_by(AttendanceMark.on_date, AttendanceMark.student_id)
        )
    )


def student_marks(
    session: Session, student_ids: Collection[uuid.UUID], *, since: dt.date | None = None
) -> list[tuple[uuid.UUID, dt.date, str]]:
    """(student, date, status) of many students, oldest first."""
    if not student_ids:
        return []
    stmt = select(AttendanceMark.student_id, AttendanceMark.on_date, AttendanceMark.status).where(
        AttendanceMark.student_id.in_(list(student_ids))
    )
    if since is not None:
        stmt = stmt.where(AttendanceMark.on_date >= since)
    rows = session.execute(stmt.order_by(AttendanceMark.student_id, AttendanceMark.on_date))
    return [(r.student_id, r.on_date, r.status) for r in rows]


# --- exams ----------------------------------------------------------------------------------------


def insert_exam(session: Session, values: Mapping[str, Any]) -> Exam:
    exam = Exam(tenant_id=current_tenant_id(session), **values)
    session.add(exam)
    session.flush()
    session.refresh(exam)
    return exam


def exam_name_taken(session: Session, academic_year_id: uuid.UUID, name: str) -> bool:
    found = session.scalar(
        select(func.count())
        .select_from(Exam)
        .where(Exam.academic_year_id == academic_year_id, func.lower(Exam.name) == name.lower())
    )
    return bool(found)


def get_exam(session: Session, exam_id: uuid.UUID) -> Exam | None:
    return session.get(Exam, exam_id)


def list_exams(session: Session, academic_year_id: uuid.UUID | None) -> list[Exam]:
    stmt = select(Exam)
    if academic_year_id is not None:
        stmt = stmt.where(Exam.academic_year_id == academic_year_id)
    return list(session.scalars(stmt.order_by(Exam.held_on, Exam.name, Exam.id)))


# --- marks ----------------------------------------------------------------------------------------


def upsert_marks(session: Session, rows: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Insert or correct marks (one per exam, student and subject); unchanged rows are left
    alone. Returns (inserted, updated)."""
    if not rows:
        return 0, 0
    tenant_id = current_tenant_id(session)
    insert = pg_insert(ExamMark).values([{"tenant_id": tenant_id, **r} for r in rows])
    excluded = insert.excluded
    stmt: Any = insert.on_conflict_do_update(
        constraint="exam_marks_one_per_subject",
        set_={
            "max_marks": excluded.max_marks,
            "marks": excluded.marks,
            "absent": excluded.absent,
            "section_id": excluded.section_id,
            "source": excluded.source,
            "recorded_by": excluded.recorded_by,
            "version": ExamMark.version + 1,
        },
        where=or_(
            ExamMark.max_marks != excluded.max_marks,
            ExamMark.marks.is_distinct_from(excluded.marks),
            ExamMark.absent != excluded.absent,
            ExamMark.section_id != excluded.section_id,
        ),
    ).returning(ExamMark.id, literal_column("(xmax = 0)").label("inserted"))
    return _written(session, stmt)


def exam_marks(
    session: Session, exam_id: uuid.UUID, student_ids: Collection[uuid.UUID]
) -> list[ExamMark]:
    if not student_ids:
        return []
    return list(
        session.scalars(
            select(ExamMark)
            .where(ExamMark.exam_id == exam_id, ExamMark.student_id.in_(list(student_ids)))
            .order_by(ExamMark.student_id, ExamMark.subject)
        )
    )


def exam_subjects(session: Session, exam_id: uuid.UUID) -> list[str]:
    """The subjects stored for an exam (every section), first written first."""
    rows = session.execute(
        select(ExamMark.subject, func.min(ExamMark.created_at).label("first"))
        .where(ExamMark.exam_id == exam_id)
        .group_by(ExamMark.subject)
        .order_by("first", ExamMark.subject)
    )
    return [r.subject for r in rows]


def results_of(
    session: Session, student_ids: Collection[uuid.UUID], academic_year_id: uuid.UUID | None
) -> list[tuple[ExamMark, Exam]]:
    """Marks of many students with their exam (one year, or every year)."""
    if not student_ids:
        return []
    stmt = (
        select(ExamMark, Exam)
        .join(Exam, (Exam.id == ExamMark.exam_id) & (Exam.tenant_id == ExamMark.tenant_id))
        .where(ExamMark.student_id.in_(list(student_ids)))
    )
    if academic_year_id is not None:
        stmt = stmt.where(Exam.academic_year_id == academic_year_id)
    rows = session.execute(stmt.order_by(Exam.held_on, Exam.id, ExamMark.subject))
    return [(r[0], r[1]) for r in rows]


# --- full export (FR-ADM-001) ---------------------------------------------------------------------


def export_tables(session: Session) -> list[RecordTable]:
    return [
        dump_table(
            session,
            AttendanceMark.__table__,
            name="attendance_marks",
            order_by=("on_date", "student_id"),
        ),
        dump_table(session, Exam.__table__, name="exams", order_by=("held_on", "id")),
        dump_table(
            session,
            ExamMark.__table__,
            name="exam_marks",
            order_by=("exam_id", "student_id", "subject"),
        ),
    ]


__all__ = [
    "current_tenant_id",
    "exam_marks",
    "exam_name_taken",
    "exam_subjects",
    "export_tables",
    "get_exam",
    "insert_exam",
    "list_exams",
    "results_of",
    "section_marks",
    "student_marks",
    "upsert_attendance",
    "upsert_marks",
]
