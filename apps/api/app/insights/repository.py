"""Database access for behaviour notes, flags, actions and settings (0035_student_insights).

Callers inside ``app.insights`` only. Every function runs in a ``core.db.tenant_session``: RLS
limits each statement to that school and ``tenant_id`` is taken from the session's context.
Ciphertext columns are read and written as bytes; plaintext never passes through here.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.insights.models import BehaviourNote, FlagAction, InsightFlag, InsightSettings

OPEN = ("open", "in_progress")


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("insights repository used outside tenant_session")
    return uuid.UUID(str(value))


# --- notes ----------------------------------------------------------------------------------------


def insert_note(session: Session, values: Mapping[str, Any]) -> BehaviourNote:
    note = BehaviourNote(tenant_id=current_tenant_id(session), **values)
    session.add(note)
    session.flush()
    session.refresh(note)
    return note


def get_note(session: Session, note_id: uuid.UUID, *, lock: bool = False) -> BehaviourNote | None:
    stmt = select(BehaviourNote).where(BehaviourNote.id == note_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def delete_note(session: Session, note_id: uuid.UUID) -> None:
    session.execute(
        delete(BehaviourNote).where(BehaviourNote.id == note_id),
        execution_options={"synchronize_session": False},
    )


def notes_of(session: Session, student_id: uuid.UUID) -> list[BehaviourNote]:
    return list(
        session.scalars(
            select(BehaviourNote)
            .where(BehaviourNote.student_id == student_id)
            .order_by(BehaviourNote.noted_on.desc(), BehaviourNote.created_at.desc())
        )
    )


def concern_dates(
    session: Session, student_ids: Collection[uuid.UUID], since: dt.date
) -> dict[uuid.UUID, list[dt.date]]:
    out: dict[uuid.UUID, list[dt.date]] = {}
    if not student_ids:
        return out
    rows = session.execute(
        select(BehaviourNote.student_id, BehaviourNote.noted_on).where(
            BehaviourNote.student_id.in_(list(student_ids)),
            BehaviourNote.category == "concern",
            BehaviourNote.noted_on >= since,
        )
    )
    for student_id, noted_on in rows:
        out.setdefault(student_id, []).append(noted_on)
    return out


def purge_notes(session: Session, before: dt.date) -> int:
    result = session.execute(
        delete(BehaviourNote).where(BehaviourNote.noted_on < before),
        execution_options={"synchronize_session": False},
    )
    return int(getattr(result, "rowcount", 0) or 0)


def stale_notes(session: Session, key_version: int, limit: int) -> list[BehaviourNote]:
    if limit <= 0:
        return []
    return list(
        session.scalars(
            select(BehaviourNote)
            .where(BehaviourNote.key_version != key_version)
            .order_by(BehaviourNote.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def set_note_ciphertext(
    session: Session, note_id: uuid.UUID, blob: bytes, key_version: int
) -> None:
    session.execute(
        update(BehaviourNote)
        .where(BehaviourNote.id == note_id)
        .values(body_ciphertext=blob, key_version=key_version)
        .execution_options(synchronize_session=False)
    )


# --- flags ----------------------------------------------------------------------------------------


def insert_flag(session: Session, values: Mapping[str, Any]) -> uuid.UUID | None:
    """Insert unless a flag for the same basis exists or the rule already has an open flag
    for the student (both unique keys; FR-EW-003): returns the new id or None."""
    stmt = (
        pg_insert(InsightFlag)
        .values(tenant_id=current_tenant_id(session), **values)
        .on_conflict_do_nothing()
        .returning(InsightFlag.id)
    )
    inserted: uuid.UUID | None = session.execute(stmt).scalar_one_or_none()
    return inserted


def get_flag(session: Session, flag_id: uuid.UUID, *, lock: bool = False) -> InsightFlag | None:
    stmt = select(InsightFlag).where(InsightFlag.id == flag_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def update_flag(session: Session, flag_id: uuid.UUID, values: Mapping[str, Any]) -> InsightFlag:
    session.execute(
        update(InsightFlag)
        .where(InsightFlag.id == flag_id)
        .values(**values, version=InsightFlag.version + 1)
        .execution_options(synchronize_session=False)
    )
    flag = get_flag(session, flag_id)
    if flag is None:  # pragma: no cover - the caller holds the row lock
        raise RuntimeError("flag vanished under its lock")
    return flag


def delete_flag(session: Session, flag_id: uuid.UUID) -> None:
    session.execute(
        delete(InsightFlag).where(InsightFlag.id == flag_id),
        execution_options={"synchronize_session": False},
    )


def open_keys(
    session: Session, student_ids: Collection[uuid.UUID]
) -> tuple[set[tuple[uuid.UUID, str]], set[tuple[uuid.UUID, str, str]]]:
    """(student, rule) pairs with an open flag, and every (student, rule, basis) raised."""
    if not student_ids:
        return set(), set()
    rows = session.execute(
        select(
            InsightFlag.student_id, InsightFlag.rule, InsightFlag.basis, InsightFlag.status
        ).where(InsightFlag.student_id.in_(list(student_ids)), InsightFlag.rule != "manual")
    )
    opened: set[tuple[uuid.UUID, str]] = set()
    raised: set[tuple[uuid.UUID, str, str]] = set()
    for student_id, rule, basis, status in rows:
        raised.add((student_id, rule, basis))
        if status in OPEN:
            opened.add((student_id, rule))
    return opened, raised


def list_flags(
    session: Session,
    *,
    student_ids: Collection[uuid.UUID] | None,
    owner: uuid.UUID | None,
    statuses: Sequence[str],
    indicator: str | None,
    section_id: uuid.UUID | None,
    student_id: uuid.UUID | None,
    overdue_before: dt.date | None,
    due_until: dt.date | None,
    after: tuple[dt.date, uuid.UUID] | None,
    limit: int,
) -> list[InsightFlag]:
    """Flags ordered by due date (soonest first); ``student_ids`` None = no scope filter."""
    stmt = select(InsightFlag).where(InsightFlag.status.in_(list(statuses)))
    if student_ids is not None:
        if not student_ids:
            return []
        stmt = stmt.where(InsightFlag.student_id.in_(list(student_ids)))
    if owner is not None:
        stmt = stmt.where(InsightFlag.owner_membership_id == owner)
    if indicator is not None:
        stmt = stmt.where(InsightFlag.indicator == indicator)
    if section_id is not None:
        stmt = stmt.where(InsightFlag.section_id == section_id)
    if student_id is not None:
        stmt = stmt.where(InsightFlag.student_id == student_id)
    if overdue_before is not None:
        stmt = stmt.where(
            InsightFlag.due_on < overdue_before, InsightFlag.first_action_at.is_(None)
        )
    if due_until is not None:
        stmt = stmt.where(InsightFlag.due_on <= due_until)
    if after is not None:
        due, last_id = after
        stmt = stmt.where(
            or_(InsightFlag.due_on > due, and_(InsightFlag.due_on == due, InsightFlag.id > last_id))
        )
    return list(session.scalars(stmt.order_by(InsightFlag.due_on, InsightFlag.id).limit(limit)))


def flags_of(session: Session, student_id: uuid.UUID) -> list[InsightFlag]:
    return list(
        session.scalars(
            select(InsightFlag)
            .where(InsightFlag.student_id == student_id)
            .order_by(InsightFlag.raised_on.desc(), InsightFlag.id)
        )
    )


def overdue_flags(session: Session, today: dt.date) -> list[InsightFlag]:
    """Open flags past their due date that nobody acted on (FR-EW-006)."""
    return list(
        session.scalars(
            select(InsightFlag)
            .where(
                InsightFlag.status == "open",
                InsightFlag.first_action_at.is_(None),
                InsightFlag.due_on < today,
            )
            .order_by(InsightFlag.due_on, InsightFlag.id)
        )
    )


def purge_closed(session: Session, before: dt.datetime) -> int:
    result = session.execute(
        delete(InsightFlag).where(InsightFlag.status == "closed", InsightFlag.closed_at < before),
        execution_options={"synchronize_session": False},
    )
    return int(getattr(result, "rowcount", 0) or 0)


def summary_rows(
    session: Session, student_ids: Collection[uuid.UUID] | None, since: dt.date
) -> list[tuple[str, dt.date, dt.date, dt.datetime | None, dt.datetime]]:
    """(status, raised_on, due_on, first_action_at, created_at) of flags raised since, or open."""
    stmt = select(
        InsightFlag.status,
        InsightFlag.raised_on,
        InsightFlag.due_on,
        InsightFlag.first_action_at,
        InsightFlag.created_at,
    ).where(or_(InsightFlag.raised_on >= since, InsightFlag.status.in_(OPEN)))
    if student_ids is not None:
        if not student_ids:
            return []
        stmt = stmt.where(InsightFlag.student_id.in_(list(student_ids)))
    return [(r[0], r[1], r[2], r[3], r[4]) for r in session.execute(stmt)]


# --- actions --------------------------------------------------------------------------------------


def insert_action(session: Session, values: Mapping[str, Any]) -> FlagAction:
    action = FlagAction(tenant_id=current_tenant_id(session), **values)
    session.add(action)
    session.flush()
    session.refresh(action)
    return action


def actions_of(
    session: Session, flag_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[FlagAction]]:
    out: dict[uuid.UUID, list[FlagAction]] = {fid: [] for fid in flag_ids}
    if not flag_ids:
        return out
    rows = session.scalars(
        select(FlagAction)
        .where(FlagAction.flag_id.in_(list(flag_ids)))
        .order_by(FlagAction.created_at, FlagAction.id)
    )
    for row in rows:
        out.setdefault(row.flag_id, []).append(row)
    return out


def stale_actions(session: Session, key_version: int, limit: int) -> list[FlagAction]:
    if limit <= 0:
        return []
    return list(
        session.scalars(
            select(FlagAction)
            .where(FlagAction.key_version.is_not(None), FlagAction.key_version != key_version)
            .order_by(FlagAction.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def set_action_ciphertext(
    session: Session, action_id: uuid.UUID, blob: bytes, key_version: int
) -> None:
    session.execute(
        update(FlagAction)
        .where(FlagAction.id == action_id)
        .values(note_ciphertext=blob, key_version=key_version)
        .execution_options(synchronize_session=False)
    )


# --- settings -------------------------------------------------------------------------------------


def get_settings(session: Session, *, lock: bool = False) -> InsightSettings | None:
    stmt = select(InsightSettings)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def save_settings(
    session: Session, rules: Mapping[str, Any], updated_by: uuid.UUID, *, new_id: uuid.UUID
) -> InsightSettings:
    """Upsert the school's one settings row (version + 1 on change)."""
    stmt = pg_insert(InsightSettings).values(
        id=new_id, tenant_id=current_tenant_id(session), rules=dict(rules), updated_by=updated_by
    )
    stmt = stmt.on_conflict_do_update(
        constraint="insight_settings_one_per_school",
        set_={
            "rules": stmt.excluded.rules,
            "updated_by": stmt.excluded.updated_by,
            "version": InsightSettings.version + 1,
        },
    )
    session.execute(stmt)
    row = get_settings(session)
    if row is None:  # pragma: no cover - just written
        raise RuntimeError("settings vanished")
    return row


# --- full export (FR-ADM-001) ---------------------------------------------------------------------


def all_notes(session: Session) -> list[BehaviourNote]:
    return list(
        session.scalars(select(BehaviourNote).order_by(BehaviourNote.noted_on, BehaviourNote.id))
    )


def all_flags(session: Session) -> list[InsightFlag]:
    return list(
        session.scalars(select(InsightFlag).order_by(InsightFlag.raised_on, InsightFlag.id))
    )


def all_actions(session: Session) -> list[FlagAction]:
    return list(session.scalars(select(FlagAction).order_by(FlagAction.created_at, FlagAction.id)))


def count_flags(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(InsightFlag)) or 0)


__all__ = [
    "actions_of",
    "concern_dates",
    "count_flags",
    "current_tenant_id",
    "delete_flag",
    "delete_note",
    "flags_of",
    "get_flag",
    "get_note",
    "get_settings",
    "insert_action",
    "insert_flag",
    "insert_note",
    "list_flags",
    "notes_of",
    "open_keys",
    "overdue_flags",
    "purge_closed",
    "purge_notes",
    "save_settings",
    "set_action_ciphertext",
    "set_note_ciphertext",
    "stale_actions",
    "stale_notes",
    "summary_rows",
    "update_flag",
]
