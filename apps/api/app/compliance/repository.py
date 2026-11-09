"""Database access for the compliance certificate register (0096_compliance_register; FC-01).

Callers inside ``app.compliance`` only. Every function runs in a ``core.db.tenant_session``:
RLS limits each statement to that school. Rows are never deleted (only the offboarding purge
removes them); a renewal supersedes the old row and a mistake is marked ``removed``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.compliance.models import ComplianceCertificate as Row
from app.core.record_tables import dump_table
from app.core.records import RecordTable

CURRENT: Final = "current"
# Older rows of one kind the detail view shows (newest first).
HISTORY_LIMIT: Final = 50


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("compliance repository used outside tenant_session")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def insert(session: Session, **values: Any) -> Row:
    row = Row(**values)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


def get(session: Session, certificate_id: uuid.UUID, *, lock: bool = False) -> Row | None:
    stmt = select(Row).where(Row.id == certificate_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def current_rows(session: Session) -> list[Row]:
    """Every current certificate of the school (by kind, then label, then id)."""
    stmt = (
        select(Row).where(Row.status == CURRENT).order_by(Row.kind, Row.label.nulls_first(), Row.id)
    )
    return list(session.scalars(stmt))


def current_of_kind(session: Session, kind: str, *, lock: bool = False) -> Row | None:
    """The current certificate of a catalogue kind (``other`` may have several: not used)."""
    stmt = select(Row).where(Row.status == CURRENT, Row.kind == kind)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt.order_by(Row.created_at.desc(), Row.id).limit(1)).one_or_none()


def history(session: Session, row: Row) -> list[Row]:
    """The other rows of the same kind (for ``other``: the chain this row renewed), newest
    first."""
    stmt = select(Row).where(Row.id != row.id)
    if row.kind == "other":
        chain: list[Row] = []
        seen = {row.id}
        target = row.id
        for _ in range(HISTORY_LIMIT):
            prev = session.scalars(select(Row).where(Row.superseded_by == target)).one_or_none()
            if prev is None or prev.id in seen:
                break
            chain.append(prev)
            seen.add(prev.id)
            target = prev.id
        return chain
    stmt = stmt.where(Row.kind == row.kind)
    return list(session.scalars(stmt.order_by(Row.created_at.desc(), Row.id).limit(HISTORY_LIMIT)))


def predecessor(session: Session, certificate_id: uuid.UUID) -> Row | None:
    """The row a renewal superseded, if any."""
    stmt = select(Row).where(Row.superseded_by == certificate_id).with_for_update()
    return session.scalars(stmt).one_or_none()


def update_row(
    session: Session, row: Row, values: Mapping[str, Any], *, expected_version: int
) -> Row | None:
    """Optimistic update: ``None`` when the row's version moved on (someone else acted)."""
    result = session.execute(
        update(Row)
        .where(Row.id == row.id, Row.version == expected_version)
        .values({**values, "version": Row.version + 1})
        .returning(Row.id),
        execution_options={"synchronize_session": False},
    )
    if result.scalar_one_or_none() is None:
        return None
    session.expire(row)
    return get(session, row.id)


def set_values(session: Session, certificate_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    """Workflow change made by this module itself (a renewal superseding the old row, a
    removal restoring it): bumps the version so a stale edit of it gets 412."""
    session.execute(
        update(Row).where(Row.id == certificate_id).values({**values, "version": Row.version + 1}),
        execution_options={"synchronize_session": False},
    )


def due_for_reminder(session: Session) -> list[Row]:
    """Current certificates with a "valid until" date (the job decides which one is due)."""
    stmt = (
        select(Row)
        .where(Row.status == CURRENT, Row.valid_until.is_not(None))
        .order_by(Row.valid_until, Row.id)
        .with_for_update(skip_locked=True)
    )
    return list(session.scalars(stmt))


def mark_reminded(session: Session, certificate_id: uuid.UUID, lead: int, day: dt.date) -> None:
    """Bookkeeping only: the row's version is not bumped (no one's edit is lost)."""
    session.execute(
        update(Row)
        .where(Row.id == certificate_id)
        .values(last_reminded_lead=lead, last_reminded_on=day),
        execution_options={"synchronize_session": False},
    )


def export_tables(session: Session) -> list[RecordTable]:
    """The school's register for its full data export."""
    return [
        dump_table(
            session,
            Row.__table__,
            name="compliance_certificates",
            order_by=("created_at", "id"),
        )
    ]


__all__ = [
    "CURRENT",
    "current_of_kind",
    "current_rows",
    "current_tenant_id",
    "due_for_reminder",
    "export_tables",
    "get",
    "history",
    "insert",
    "mark_reminded",
    "now",
    "predecessor",
    "set_values",
    "update_row",
]
