"""Database access for ``ops.notifications`` (only ``app.notifications.service`` calls this).

Every function runs in a ``tenant_session``: RLS limits rows to the current school, and each
reader query is additionally filtered by the recipient membership (a user only ever sees their
own notifications).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import RowMapping, and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.notifications.models import notifications as n


def current_tenant(session: Session) -> uuid.UUID | None:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    return uuid.UUID(str(value)) if value is not None else None


def insert_many(session: Session, rows: Sequence[dict[str, Any]]) -> int:
    """Insert; a row whose (recipient, dedupe_key) already exists is skipped."""
    if not rows:
        return 0
    stmt = (
        pg_insert(n)
        .values(list(rows))
        .on_conflict_do_nothing(
            index_elements=[n.c.tenant_id, n.c.recipient_membership_id, n.c.dedupe_key],
            index_where=n.c.dedupe_key.is_not(None),
        )
        .returning(n.c.id)
    )
    return len(session.execute(stmt).all())


def list_for_recipient(
    session: Session,
    membership_id: uuid.UUID,
    *,
    limit: int,
    after: tuple[dt.datetime, uuid.UUID] | None,
    unread_only: bool,
) -> list[RowMapping]:
    stmt = select(n).where(n.c.recipient_membership_id == membership_id)
    if unread_only:
        stmt = stmt.where(n.c.read_at.is_(None))
    if after is not None:
        created, last_id = after
        stmt = stmt.where(
            or_(n.c.created_at < created, and_(n.c.created_at == created, n.c.id < last_id))
        )
    stmt = stmt.order_by(n.c.created_at.desc(), n.c.id.desc()).limit(limit)
    return list(session.execute(stmt).mappings())


def count_unread(session: Session, membership_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(n)
        .where(n.c.recipient_membership_id == membership_id, n.c.read_at.is_(None))
    )
    return int(session.execute(stmt).scalar_one())


def mark_read(
    session: Session, membership_id: uuid.UUID, notification_id: uuid.UUID
) -> RowMapping | None:
    """Mark one of the recipient's notifications read (idempotent: keeps the first read time)."""
    stmt = (
        update(n)
        .where(n.c.id == notification_id, n.c.recipient_membership_id == membership_id)
        .values(read_at=func.coalesce(n.c.read_at, func.now()))
        .returning(*n.c)
    )
    return session.execute(stmt).mappings().one_or_none()


def mark_all_read(session: Session, membership_id: uuid.UUID) -> int:
    stmt = (
        update(n)
        .where(n.c.recipient_membership_id == membership_id, n.c.read_at.is_(None))
        .values(read_at=func.now())
        .returning(n.c.id)
    )
    return len(session.execute(stmt).all())


def purge_read_before(session: Session, before: dt.datetime) -> int:
    stmt = delete(n).where(n.c.read_at.is_not(None), n.c.read_at < before).returning(n.c.id)
    return len(session.execute(stmt).all())
