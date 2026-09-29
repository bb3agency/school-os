"""Database access for circular readings, suggestions, tasks and notices (0034_circulars).

Callers inside ``app.circulars`` only. Every function runs in a ``core.db.tenant_session``: RLS
limits each statement to that school and ``tenant_id`` is taken from the session's context.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.circulars.models import CircularReading, CircularSuggestion, ParentNotice, Task
from app.core.record_tables import dump_table
from app.core.records import RecordTable


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("circulars repository used outside tenant_session")
    return uuid.UUID(str(value))


# --- readings -------------------------------------------------------------------------------------


def insert_reading(session: Session, values: Mapping[str, Any]) -> uuid.UUID | None:
    """Insert a reading unless the version already has one (idempotent per version)."""
    stmt = (
        pg_insert(CircularReading)
        .values(tenant_id=current_tenant_id(session), **values)
        .on_conflict_do_nothing(constraint="circular_readings_one_per_version")
        .returning(CircularReading.id)
    )
    inserted: uuid.UUID | None = session.execute(stmt).scalar_one_or_none()
    return inserted


def reading_for_version(session: Session, version_id: uuid.UUID) -> CircularReading | None:
    return session.execute(
        select(CircularReading).where(CircularReading.version_id == version_id)
    ).scalar_one_or_none()


def lock_reading(session: Session, reading_id: uuid.UUID) -> CircularReading | None:
    return session.execute(
        select(CircularReading).where(CircularReading.id == reading_id).with_for_update()
    ).scalar_one_or_none()


def latest_readings(
    session: Session, document_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, CircularReading]:
    """The newest version's reading of each document (if any)."""
    if not document_ids:
        return {}
    rows = (
        session.execute(
            select(CircularReading)
            .where(CircularReading.document_id.in_(list(document_ids)))
            .order_by(CircularReading.document_id, CircularReading.version_no.desc())
            .distinct(CircularReading.document_id)
        )
        .scalars()
        .all()
    )
    return {r.document_id: r for r in rows}


def update_reading(session: Session, reading_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    session.execute(
        update(CircularReading)
        .where(CircularReading.id == reading_id)
        .values(**values, version=CircularReading.version + 1)
    )


def insert_suggestions(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    if rows:
        tenant_id = current_tenant_id(session)
        session.execute(
            pg_insert(CircularSuggestion), [{"tenant_id": tenant_id, **r} for r in rows]
        )


def suggestions_of(
    session: Session, reading_ids: Collection[uuid.UUID]
) -> list[CircularSuggestion]:
    if not reading_ids:
        return []
    return list(
        session.execute(
            select(CircularSuggestion)
            .where(CircularSuggestion.reading_id.in_(list(reading_ids)))
            .order_by(CircularSuggestion.reading_id, CircularSuggestion.position)
        ).scalars()
    )


def lock_suggestion(
    session: Session, suggestion_id: uuid.UUID
) -> tuple[CircularSuggestion, CircularReading] | None:
    row = session.execute(
        select(CircularSuggestion, CircularReading)
        .join(CircularReading, CircularReading.id == CircularSuggestion.reading_id)
        .where(CircularSuggestion.id == suggestion_id)
        .with_for_update(of=CircularSuggestion)
    ).first()
    return (row[0], row[1]) if row else None


def update_suggestion(
    session: Session, suggestion_id: uuid.UUID, values: Mapping[str, Any]
) -> None:
    session.execute(
        update(CircularSuggestion)
        .where(CircularSuggestion.id == suggestion_id)
        .values(**values, version=CircularSuggestion.version + 1)
    )


def open_suggestion_counts(
    session: Session, reading_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not reading_ids:
        return {}
    rows = session.execute(
        select(CircularSuggestion.reading_id, func.count())
        .where(
            CircularSuggestion.reading_id.in_(list(reading_ids)),
            CircularSuggestion.status == "suggested",
        )
        .group_by(CircularSuggestion.reading_id)
    ).all()
    return {r[0]: int(r[1]) for r in rows}


# --- tasks ----------------------------------------------------------------------------------------


def insert_task(session: Session, values: Mapping[str, Any]) -> Task:
    task = Task(tenant_id=current_tenant_id(session), **values)
    session.add(task)
    session.flush()
    session.refresh(task)
    return task


def get_task(session: Session, task_id: uuid.UUID, *, lock: bool = False) -> Task | None:
    stmt = select(Task).where(Task.id == task_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def update_task(session: Session, task_id: uuid.UUID, values: Mapping[str, Any]) -> Task:
    session.execute(
        update(Task).where(Task.id == task_id).values(**values, version=Task.version + 1)
    )
    task = get_task(session, task_id)
    if task is None:
        raise LookupError("task vanished during update")
    session.refresh(task)
    return task


def task_counts(session: Session, document_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not document_ids:
        return {}
    rows = session.execute(
        select(Task.document_id, func.count())
        .where(Task.document_id.in_(list(document_ids)), Task.status != "cancelled")
        .group_by(Task.document_id)
    ).all()
    return {r[0]: int(r[1]) for r in rows if r[0] is not None}


def list_tasks(
    session: Session,
    *,
    owner: uuid.UUID | None,
    statuses: Collection[str],
    document_id: uuid.UUID | None,
    due_before: dt.date | None,
    due_from: dt.date | None,
    after: tuple[dt.date, uuid.UUID] | None,
    limit: int,
) -> list[Task]:
    """Tasks ordered by due date then id (cursor ``after`` = the last row's pair)."""
    stmt = select(Task)
    if owner is not None:
        stmt = stmt.where(Task.owner_membership_id == owner)
    if statuses:
        stmt = stmt.where(Task.status.in_(list(statuses)))
    if document_id is not None:
        stmt = stmt.where(Task.document_id == document_id)
    if due_before is not None:
        stmt = stmt.where(Task.due_on < due_before)
    if due_from is not None:
        stmt = stmt.where(Task.due_on >= due_from)
    if after is not None:
        day, last = after
        stmt = stmt.where(or_(Task.due_on > day, and_(Task.due_on == day, Task.id > last)))
    return list(session.scalars(stmt.order_by(Task.due_on, Task.id).limit(limit)))


def tasks_to_remind(session: Session, *, until: dt.date) -> list[Task]:
    """Open or in-progress tasks due on or before ``until`` (reminders, FR-TASK-007)."""
    return list(
        session.execute(
            select(Task)
            .where(Task.status.in_(("open", "in_progress")), Task.due_on <= until)
            .order_by(Task.due_on, Task.id)
        ).scalars()
    )


# --- notices --------------------------------------------------------------------------------------


def insert_notice(session: Session, values: Mapping[str, Any]) -> ParentNotice:
    notice = ParentNotice(tenant_id=current_tenant_id(session), **values)
    session.add(notice)
    session.flush()
    session.refresh(notice)
    return notice


def get_notice(
    session: Session, notice_id: uuid.UUID, *, lock: bool = False
) -> ParentNotice | None:
    stmt = select(ParentNotice).where(ParentNotice.id == notice_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def update_notice(
    session: Session, notice_id: uuid.UUID, values: Mapping[str, Any]
) -> ParentNotice:
    session.execute(
        update(ParentNotice)
        .where(ParentNotice.id == notice_id)
        .values(**values, version=ParentNotice.version + 1)
    )
    notice = get_notice(session, notice_id)
    if notice is None:
        raise LookupError("notice vanished during update")
    session.refresh(notice)
    return notice


def list_notices(
    session: Session,
    *,
    status: str | None,
    after: tuple[dt.datetime, uuid.UUID] | None,
    limit: int,
) -> list[ParentNotice]:
    stmt = select(ParentNotice)
    if status is not None:
        stmt = stmt.where(ParentNotice.status == status)
    if after is not None:
        moment, last = after
        stmt = stmt.where(
            or_(
                ParentNotice.created_at < moment,
                and_(ParentNotice.created_at == moment, ParentNotice.id < last),
            )
        )
    stmt = stmt.order_by(ParentNotice.created_at.desc(), ParentNotice.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


# --- full data export (FR-ADM-001) ---------------------------------------------------------------


def export_tables(session: Session) -> list[RecordTable]:
    """The module's tables of the current school for its full data export (FR-ADM-001)."""
    return [
        dump_table(
            session,
            CircularReading.__table__,
            name="circular_readings",
            order_by=("created_at", "id"),
        ),
        dump_table(
            session,
            CircularSuggestion.__table__,
            name="circular_suggestions",
            order_by=("reading_id", "position", "id"),
        ),
        dump_table(session, Task.__table__, name="tasks", order_by=("created_at", "id")),
        dump_table(
            session, ParentNotice.__table__, name="parent_notices", order_by=("created_at", "id")
        ),
    ]


__all__ = [
    "current_tenant_id",
    "export_tables",
    "get_notice",
    "get_task",
    "insert_notice",
    "insert_reading",
    "insert_suggestions",
    "insert_task",
    "latest_readings",
    "list_notices",
    "list_tasks",
    "lock_reading",
    "lock_suggestion",
    "open_suggestion_counts",
    "reading_for_version",
    "suggestions_of",
    "task_counts",
    "tasks_to_remind",
    "update_notice",
    "update_reading",
    "update_suggestion",
    "update_task",
]
