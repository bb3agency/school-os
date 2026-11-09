"""Database access for attendance, exams and marks (0035_student_insights).

Callers inside ``app.academics`` only. Every function runs in a ``core.db.tenant_session``: RLS
limits each statement to that school and ``tenant_id`` is taken from the session's context.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import delete, func, insert, literal_column, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.academics.models import (
    AttendanceMark,
    CalendarEntry,
    Exam,
    ExamMark,
    ExamMarkHistory,
    SchoolCalendar,
)
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


def lock_attendance_days(session: Session, dates: Collection[dt.date]) -> None:
    """Serialise attendance writes of the current school per date until the transaction ends
    (transaction-level advisory locks, taken in date order so two writers cannot deadlock), so
    the ``expected_version`` check and the upsert see no write in between (XA-06)."""
    for day in sorted(set(dates)):
        session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended("
                "'sis.attendance_marks:' || core.current_tenant()::text || ':' || :d, 0))"
            ),
            {"d": day.isoformat()},
        )


def lock_exam_marks(session: Session, exam_id: uuid.UUID) -> None:
    """Serialise marks writes of one exam until the transaction ends (transaction-level
    advisory lock), so the ``expected_version`` check and the upsert see no write in between
    (YA-05)."""
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended("
            "'sis.exam_marks:' || core.current_tenant()::text || ':' || :e, 0))"
        ),
        {"e": str(exam_id)},
    )


def attendance_versions(
    session: Session, student_ids: Collection[uuid.UUID], dates: Collection[dt.date]
) -> dict[tuple[uuid.UUID, dt.date], int]:
    """The stored version of every (student, date) mark among ``student_ids`` x ``dates``."""
    if not student_ids or not dates:
        return {}
    rows = session.execute(
        select(AttendanceMark.student_id, AttendanceMark.on_date, AttendanceMark.version).where(
            AttendanceMark.student_id.in_(list(student_ids)),
            AttendanceMark.on_date.in_(list(dates)),
        )
    ).all()
    return {(r.student_id, r.on_date): int(r.version) for r in rows}


def marks_in_other_sections(
    session: Session,
    section_id: uuid.UUID,
    student_ids: Collection[uuid.UUID],
    first: dt.date,
    last: dt.date,
) -> set[tuple[uuid.UUID, dt.date]]:
    """(student, date) pairs between ``first`` and ``last`` already recorded under a section
    other than ``section_id`` (the section the student was in that day)."""
    if not student_ids:
        return set()
    rows = session.execute(
        select(AttendanceMark.student_id, AttendanceMark.on_date).where(
            AttendanceMark.student_id.in_(list(student_ids)),
            AttendanceMark.on_date.between(first, last),
            AttendanceMark.section_id != section_id,
        )
    ).all()
    return {(r.student_id, r.on_date) for r in rows}


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


# Same name in the same year: exact (0035) or differing only in case (0049, audit 2026-10-06).
_EXAM_NAME_CONSTRAINTS = frozenset({"exams_name_per_year", "exams_name_per_year_ci"})


def students_with_status(session: Session, on_date: dt.date, status: str) -> list[uuid.UUID]:
    """Students marked ``status`` on ``on_date`` (one row per student and day)."""
    stmt = (
        select(AttendanceMark.student_id)
        .where(AttendanceMark.on_date == on_date, AttendanceMark.status == status)
        .order_by(AttendanceMark.student_id)
    )
    return list(session.scalars(stmt))


def insert_exam(session: Session, values: Mapping[str, Any]) -> Exam | None:
    """The new exam, or ``None`` when a concurrent request created one with the same name
    (whatever its case) in the same year first (``exams_name_per_year`` /
    ``exams_name_per_year_ci``; the savepoint keeps the transaction usable)."""
    exam = Exam(tenant_id=current_tenant_id(session), **values)
    try:
        with session.begin_nested():
            session.add(exam)
            session.flush()
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        if getattr(diag, "constraint_name", None) in _EXAM_NAME_CONSTRAINTS:
            return None
        raise
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


def exam_for_update(session: Session, exam_id: uuid.UUID) -> Exam | None:
    """The exam, row-locked until the transaction ends and re-read (a lock or unlock that
    committed in between is seen; FT-01)."""
    return session.scalars(
        select(Exam).where(Exam.id == exam_id).with_for_update(),
        execution_options={"populate_existing": True},
    ).one_or_none()


def update_exam_lock(session: Session, exam_id: uuid.UUID, values: Mapping[str, Any]) -> Exam:
    """Set the lock columns of an exam (``locked_*``/``unlocked_*``/``unlock_reason``) and bump
    its version."""
    return session.scalars(
        update(Exam)
        .where(Exam.id == exam_id)
        .values(**values, version=Exam.version + 1)
        .returning(Exam),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one()


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


def insert_mark_history(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    """Append history rows (one per changed mark; FT-01). Never updated or deleted by the app."""
    if not rows:
        return
    tenant_id = current_tenant_id(session)
    session.execute(insert(ExamMarkHistory).values([{"tenant_id": tenant_id, **r} for r in rows]))


def mark_history(
    session: Session, exam_id: uuid.UUID, student_ids: Collection[uuid.UUID], *, limit: int
) -> list[ExamMarkHistory]:
    """The latest ``limit`` changes of an exam's marks for ``student_ids``, newest first."""
    if not student_ids:
        return []
    return list(
        session.scalars(
            select(ExamMarkHistory)
            .where(
                ExamMarkHistory.exam_id == exam_id,
                ExamMarkHistory.student_id.in_(list(student_ids)),
            )
            .order_by(ExamMarkHistory.changed_at.desc(), ExamMarkHistory.id.desc())
            .limit(limit)
        )
    )


def marks_changed_after_lock(
    session: Session, exam_id: uuid.UUID, student_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Ids of the marks of ``student_ids`` changed after the exam was first locked."""
    if not student_ids:
        return set()
    return set(
        session.scalars(
            select(ExamMarkHistory.mark_id)
            .where(
                ExamMarkHistory.exam_id == exam_id,
                ExamMarkHistory.student_id.in_(list(student_ids)),
                ExamMarkHistory.after_lock.is_(True),
            )
            .distinct()
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


# --- data-principal access report (FR-DPDP-009) ---------------------------------------------------


def attendance_by_month(session: Session, student_id: uuid.UUID) -> list[tuple[dt.date, str, int]]:
    """(first day of the month, status, count) of one student's attendance, newest month
    first."""
    month = func.date_trunc("month", AttendanceMark.on_date).label("month")
    rows = session.execute(
        select(month, AttendanceMark.status, func.count().label("n"))
        .where(AttendanceMark.student_id == student_id)
        .group_by(month, AttendanceMark.status)
        .order_by(month.desc(), AttendanceMark.status)
    )
    out: list[tuple[dt.date, str, int]] = []
    for r in rows:
        first: dt.date | dt.datetime = r.month
        day = first.date() if isinstance(first, dt.datetime) else first
        out.append((day, r.status, int(r.n)))
    return out


def marks_of_student(
    session: Session, student_id: uuid.UUID, *, limit: int
) -> tuple[list[tuple[ExamMark, Exam]], int]:
    """At most ``limit`` marks of one student with their exam, latest exam first, and the full
    count."""
    total = int(
        session.scalar(
            select(func.count()).select_from(ExamMark).where(ExamMark.student_id == student_id)
        )
        or 0
    )
    if total == 0:
        return [], 0
    rows = session.execute(
        select(ExamMark, Exam)
        .join(Exam, (Exam.id == ExamMark.exam_id) & (Exam.tenant_id == ExamMark.tenant_id))
        .where(ExamMark.student_id == student_id)
        .order_by(Exam.held_on.desc(), Exam.name, Exam.id, ExamMark.subject)
        .limit(limit)
    )
    return [(r[0], r[1]) for r in rows], total


# --- school calendar (FT-06; 0088_school_calendar) -----------------------------------------------


def get_calendar(
    session: Session, academic_year_id: uuid.UUID, *, for_update: bool = False
) -> SchoolCalendar | None:
    stmt = select(SchoolCalendar).where(SchoolCalendar.academic_year_id == academic_year_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def insert_calendar(session: Session, values: Mapping[str, Any]) -> SchoolCalendar:
    """Insert a year's calendar; a concurrent insert of the same year wins and this one reads
    it (``ON CONFLICT DO NOTHING``)."""
    tenant_id = current_tenant_id(session)
    stmt = (
        pg_insert(SchoolCalendar)
        .values(tenant_id=tenant_id, **values)
        .on_conflict_do_nothing(constraint="school_calendars_year_key")
    )
    session.execute(stmt)
    row = get_calendar(session, values["academic_year_id"], for_update=True)
    if row is None:  # pragma: no cover - the row was just written in this transaction
        raise RuntimeError("calendar row missing after write")
    return row


def update_calendar(
    session: Session, calendar_id: uuid.UUID, values: Mapping[str, Any]
) -> SchoolCalendar:
    session.execute(
        update(SchoolCalendar)
        .where(SchoolCalendar.id == calendar_id)
        .values(**values, version=SchoolCalendar.version + 1)
    )
    row = session.get(SchoolCalendar, calendar_id, populate_existing=True)
    if row is None:  # pragma: no cover - the row was just written in this transaction
        raise RuntimeError("calendar row missing after write")
    return row


def calendar_entries(session: Session, academic_year_id: uuid.UUID) -> list[CalendarEntry]:
    return list(
        session.scalars(
            select(CalendarEntry)
            .where(CalendarEntry.academic_year_id == academic_year_id)
            .order_by(
                CalendarEntry.starts_on, CalendarEntry.class_id.nulls_first(), CalendarEntry.id
            )
        )
    )


def insert_calendar_entry(session: Session, values: Mapping[str, Any]) -> CalendarEntry:
    tenant_id = current_tenant_id(session)
    session.execute(insert(CalendarEntry).values(tenant_id=tenant_id, **values))
    row = session.get(CalendarEntry, values["id"])
    if row is None:  # pragma: no cover - the row was just written in this transaction
        raise RuntimeError("calendar row missing after write")
    return row


def delete_calendar_entry(session: Session, entry_id: uuid.UUID) -> None:
    session.execute(delete(CalendarEntry).where(CalendarEntry.id == entry_id))
    session.expire_all()


def marks_between(
    session: Session, first: dt.date, last: dt.date, class_section_ids: Collection[uuid.UUID] | None
) -> int:
    """Attendance marks dated ``first..last`` (of these sections; all when None)."""
    stmt = (
        select(func.count())
        .select_from(AttendanceMark)
        .where(AttendanceMark.on_date >= first, AttendanceMark.on_date <= last)
    )
    if class_section_ids is not None:
        if not class_section_ids:
            return 0
        stmt = stmt.where(AttendanceMark.section_id.in_(class_section_ids))
    return int(session.scalar(stmt) or 0)


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
        dump_table(
            session,
            ExamMarkHistory.__table__,
            name="exam_mark_history",
            order_by=("exam_id", "changed_at", "id"),
        ),
        dump_table(
            session,
            SchoolCalendar.__table__,
            name="school_calendars",
            order_by=("academic_year_id",),
        ),
        dump_table(
            session,
            CalendarEntry.__table__,
            name="calendar_entries",
            order_by=("academic_year_id", "starts_on", "id"),
        ),
    ]


__all__ = [
    "attendance_by_month",
    "attendance_versions",
    "calendar_entries",
    "current_tenant_id",
    "delete_calendar_entry",
    "exam_for_update",
    "exam_marks",
    "exam_name_taken",
    "exam_subjects",
    "export_tables",
    "get_calendar",
    "get_exam",
    "insert_calendar",
    "insert_calendar_entry",
    "insert_exam",
    "insert_mark_history",
    "list_exams",
    "lock_attendance_days",
    "lock_exam_marks",
    "mark_history",
    "marks_between",
    "marks_changed_after_lock",
    "marks_of_student",
    "results_of",
    "section_marks",
    "student_marks",
    "update_calendar",
    "update_exam_lock",
    "upsert_attendance",
    "upsert_marks",
]
