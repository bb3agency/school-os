"""Database access for the year-end progression pack (FT-08, 0088_year_end; FR-YRE-*).

Callers inside ``app.academics`` only (import-linter: academics-internals). Every function runs
in a ``core.db.tenant_session``: RLS limits each statement to that school.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.academics.models import AttendanceMark, YearResultRow


def section_dates(
    session: Session, section_ids: Collection[uuid.UUID], first: dt.date, last: dt.date
) -> dict[uuid.UUID, list[dt.date]]:
    """Dates with at least one attendance mark per section ("school days" until the school
    calendar, FT-06, gives working days)."""
    if not section_ids:
        return {}
    rows = session.execute(
        select(AttendanceMark.section_id, AttendanceMark.on_date)
        .where(
            AttendanceMark.section_id.in_(list(section_ids)),
            AttendanceMark.on_date >= first,
            AttendanceMark.on_date <= last,
        )
        .group_by(AttendanceMark.section_id, AttendanceMark.on_date)
        .order_by(AttendanceMark.section_id, AttendanceMark.on_date)
    )
    out: dict[uuid.UUID, list[dt.date]] = {}
    for section_id, on_date in rows:
        out.setdefault(section_id, []).append(on_date)
    return out


def attended_days(
    session: Session,
    student_ids: Collection[uuid.UUID],
    first: dt.date,
    last: dt.date,
    statuses: Collection[str],
) -> dict[uuid.UUID, int]:
    """Days each student was marked with one of ``statuses`` between ``first`` and ``last``."""
    if not student_ids:
        return {}
    rows = session.execute(
        select(AttendanceMark.student_id, func.count())
        .where(
            AttendanceMark.student_id.in_(list(student_ids)),
            AttendanceMark.on_date >= first,
            AttendanceMark.on_date <= last,
            AttendanceMark.status.in_(list(statuses)),
        )
        .group_by(AttendanceMark.student_id)
    )
    return {sid: int(count) for sid, count in rows}


def results_of_year(
    session: Session,
    academic_year_id: uuid.UUID,
    student_ids: Collection[uuid.UUID] | None = None,
    *,
    lock: bool = False,
) -> dict[uuid.UUID, YearResultRow]:
    """Confirmed results of one year (all of it, or of ``student_ids``) by student."""
    stmt = select(YearResultRow).where(YearResultRow.academic_year_id == academic_year_id)
    if student_ids is not None:
        if not student_ids:
            return {}
        stmt = stmt.where(YearResultRow.student_id.in_(list(student_ids)))
    if lock:
        stmt = stmt.with_for_update()
    rows = session.scalars(stmt, execution_options={"populate_existing": True})
    return {r.student_id: r for r in rows}


def insert_result(session: Session, values: Mapping[str, Any]) -> YearResultRow | None:
    """Insert one result; ``None`` when another transaction confirmed it first."""
    return session.scalars(
        pg_insert(YearResultRow)
        .values(**values)
        .on_conflict_do_nothing(constraint="year_results_one_per_year")
        .returning(YearResultRow)
    ).one_or_none()


def update_result(
    session: Session, result_id: uuid.UUID, *, expected_version: int, values: Mapping[str, Any]
) -> YearResultRow | None:
    """Update + bump version; ``None`` when ``expected_version`` is stale."""
    return session.scalars(
        update(YearResultRow)
        .where(YearResultRow.id == result_id, YearResultRow.version == expected_version)
        .values(**values, version=YearResultRow.version + 1, updated_at=func.now())
        .returning(YearResultRow),
        execution_options={"synchronize_session": False},
    ).one_or_none()


def results_of_student(session: Session, student_id: uuid.UUID) -> list[YearResultRow]:
    """Every confirmed result of one student, oldest year first by confirmation (access
    report)."""
    return list(
        session.scalars(
            select(YearResultRow)
            .where(YearResultRow.student_id == student_id)
            .order_by(YearResultRow.confirmed_at, YearResultRow.id)
        )
    )


__all__ = [
    "attended_days",
    "insert_result",
    "results_of_student",
    "results_of_year",
    "section_dates",
    "update_result",
]
