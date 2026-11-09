"""Database access for the weekly brief (only ``app.brief.service`` calls this).

Every function runs in a ``tenant_session``: RLS limits rows to the current school.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import RowMapping, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.brief.models import brief_preferences as p
from app.brief.models import weekly_briefs as b


def current_tenant(session: Session) -> uuid.UUID | None:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    return uuid.UUID(str(value)) if value is not None else None


def insert_brief(
    session: Session,
    *,
    brief_id: uuid.UUID,
    tenant_id: uuid.UUID,
    week_start: dt.date,
    counts: Mapping[str, Any],
) -> RowMapping | None:
    """Store a week's brief; None when that week is stored already (a concurrent run won)."""
    stmt = (
        pg_insert(b)
        .values(id=brief_id, tenant_id=tenant_id, week_start=week_start, counts=dict(counts))
        .on_conflict_do_nothing(constraint="weekly_briefs_once")
        .returning(*b.c)
    )
    return session.execute(stmt).mappings().first()


def get_brief(session: Session, brief_id: uuid.UUID) -> RowMapping | None:
    return session.execute(select(b).where(b.c.id == brief_id)).mappings().first()


def brief_of_week(session: Session, week_start: dt.date) -> RowMapping | None:
    return session.execute(select(b).where(b.c.week_start == week_start)).mappings().first()


def latest(session: Session) -> RowMapping | None:
    stmt = select(b).order_by(b.c.week_start.desc()).limit(1)
    return session.execute(stmt).mappings().first()


def previous(session: Session, week_start: dt.date) -> RowMapping | None:
    """The newest stored brief of a week before ``week_start``."""
    stmt = select(b).where(b.c.week_start < week_start).order_by(b.c.week_start.desc()).limit(1)
    return session.execute(stmt).mappings().first()


def history(session: Session, limit: int) -> list[RowMapping]:
    stmt = select(b.c.id, b.c.week_start, b.c.built_at).order_by(b.c.week_start.desc()).limit(limit)
    return list(session.execute(stmt).mappings())


def set_notified(session: Session, brief_id: uuid.UUID, notified: int) -> None:
    """How many in-app notices the week's brief sent (the only column ``sos_app`` may update)."""
    session.execute(update(b).where(b.c.id == brief_id).values(notified=notified))


def delete_older_than(session: Session, week_start: dt.date) -> int:
    result = session.execute(delete(b).where(b.c.week_start < week_start))
    return int(getattr(result, "rowcount", 0) or 0)


def get_preferences(session: Session, membership_id: uuid.UUID) -> RowMapping | None:
    stmt = select(p).where(p.c.membership_id == membership_id)
    return session.execute(stmt).mappings().first()


def preferences_of(session: Session) -> dict[uuid.UUID, RowMapping]:
    return {r["membership_id"]: r for r in session.execute(select(p)).mappings()}


def insert_preferences(
    session: Session, *, tenant_id: uuid.UUID, membership_id: uuid.UUID, in_app: bool, email: bool
) -> RowMapping:
    stmt = (
        pg_insert(p)
        .values(tenant_id=tenant_id, membership_id=membership_id, in_app=in_app, email=email)
        .returning(*p.c)
    )
    return session.execute(stmt).mappings().one()


def update_preferences(
    session: Session, membership_id: uuid.UUID, *, version: int, in_app: bool, email: bool
) -> RowMapping | None:
    """Update when ``version`` matches; None otherwise (stale)."""
    stmt = (
        update(p)
        .where(p.c.membership_id == membership_id, p.c.version == version)
        .values(in_app=in_app, email=email, version=p.c.version + 1)
        .returning(*p.c)
    )
    return session.execute(stmt).mappings().first()


def delete_preferences(session: Session, membership_id: uuid.UUID) -> int:
    result = session.execute(delete(p).where(p.c.membership_id == membership_id))
    return int(getattr(result, "rowcount", 0) or 0)


def count_preferences(session: Session, membership_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(p).where(p.c.membership_id == membership_id)
    return int(session.execute(stmt).scalar_one())
