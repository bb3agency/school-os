"""DB access for the follow-up log (FO-01) and the UDISE+ to-do ticks (FO-03). Private to
``app.dq`` (import-linter ``dq-internals``); RLS limits every statement to the current school."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import RowMapping, delete, func, select, update
from sqlalchemy.orm import Session

from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.dq import followup_models as m
from app.dq.repository import current_tenant

F = m.dq_followups
M = m.udise_movement_marks


# --- follow-ups (append-only) -------------------------------------------------------------------


def insert_followups(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    tenant_id = current_tenant(session)
    session.execute(F.insert(), [{"tenant_id": tenant_id, **row} for row in rows])


def followups_of(
    session: Session, profile_key: str, student_ids: Collection[uuid.UUID]
) -> list[RowMapping]:
    """Every step recorded for these students and profile, oldest first."""
    ids = list(student_ids)
    if not ids:
        return []
    stmt = (
        select(F)
        .where(F.c.profile_key == profile_key, F.c.student_id.in_(ids))
        .order_by(F.c.occurred_on, F.c.recorded_at, F.c.id)
    )
    return list(session.execute(stmt).mappings())


# --- UDISE+ to-do ticks -------------------------------------------------------------------------


def marks(session: Session, item_keys: Collection[str]) -> dict[str, RowMapping]:
    keys = list(item_keys)
    if not keys:
        return {}
    stmt = select(M).where(M.c.item_key.in_(keys))
    return {row["item_key"]: row for row in session.execute(stmt).mappings()}


def mark(session: Session, item_key: str, *, lock: bool = False) -> RowMapping | None:
    stmt = select(M).where(M.c.item_key == item_key)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).mappings().one_or_none()


def insert_mark(session: Session, values: Mapping[str, Any]) -> RowMapping:
    stmt = M.insert().values(tenant_id=current_tenant(session), **values).returning(*M.c)
    return session.execute(stmt).mappings().one()


def update_mark(
    session: Session, mark_id: uuid.UUID, version: int, values: Mapping[str, Any]
) -> RowMapping | None:
    stmt = (
        update(M)
        .where(M.c.id == mark_id, M.c.version == version)
        .values(**values, version=M.c.version + 1)
        .returning(*M.c)
    )
    return session.execute(stmt).mappings().one_or_none()


def delete_mark(session: Session, mark_id: uuid.UUID, version: int) -> bool:
    stmt = delete(M).where(M.c.id == mark_id, M.c.version == version).returning(M.c.id)
    return session.execute(stmt).first() is not None


def subject_followup_counts(session: Session, student_id: uuid.UUID) -> list[RowMapping]:
    """Steps recorded for one student counted by fix owner and step, with the latest date
    (access report, FR-DPDP-009)."""
    last = func.max(F.c.occurred_on).label("last_on")
    stmt = (
        select(F.c.fix_owner, F.c.event, func.count().label("n"), last)
        .where(F.c.student_id == student_id)
        .group_by(F.c.fix_owner, F.c.event)
        .order_by(last.desc(), F.c.fix_owner, F.c.event)
    )
    return list(session.execute(stmt).mappings())


def export_record_tables(session: Session) -> list[RecordTable]:
    """The follow-up log and the ticks of the current school (FR-ADM-001): dates, codes and
    ids only."""
    return [
        dump_table(session, F, name="dq_followups", order_by=("recorded_at", "id")),
        dump_table(session, M, name="udise_movement_marks", order_by=("created_at", "id")),
    ]
