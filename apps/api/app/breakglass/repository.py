"""Database access for ``ops.break_glass_grants`` (only ``app.breakglass.service`` calls this).

Every function runs in a ``tenant_session`` (RLS: the current school only).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import RowMapping, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.breakglass.models import grants as g

PUSHABLE = ("active", "denied", "expired", "revoked")


def insert_if_new(session: Session, values: Mapping[str, Any]) -> RowMapping | None:
    """Insert a grant for a control-plane request; ``None`` if that request is already here."""
    stmt = (
        pg_insert(g)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[g.c.tenant_id, g.c.platform_request_id])
        .returning(g)
    )
    return session.execute(stmt).mappings().one_or_none()


def get(session: Session, grant_id: uuid.UUID, *, for_update: bool = False) -> RowMapping | None:
    stmt = select(g).where(g.c.id == grant_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).mappings().one_or_none()


def get_by_request(session: Session, request_id: uuid.UUID) -> RowMapping | None:
    return (
        session.execute(select(g).where(g.c.platform_request_id == request_id)).mappings().first()
    )


def update_grant(session: Session, grant_id: uuid.UUID, values: Mapping[str, Any]) -> RowMapping:
    stmt = update(g).where(g.c.id == grant_id).values(**values).returning(g)
    return session.execute(stmt).mappings().one()


def list_page(
    session: Session,
    *,
    limit: int,
    after: tuple[dt.datetime, uuid.UUID] | None,
    status: str | None,
) -> list[RowMapping]:
    stmt = select(g)
    if status is not None:
        stmt = stmt.where(g.c.status == status)
    if after is not None:
        created, last_id = after
        stmt = stmt.where(
            or_(g.c.created_at < created, and_(g.c.created_at == created, g.c.id < last_id))
        )
    stmt = stmt.order_by(g.c.created_at.desc(), g.c.id.desc()).limit(limit)
    return list(session.execute(stmt).mappings())


def list_due(session: Session, now: dt.datetime) -> list[RowMapping]:
    """Approved/active grants whose window has passed (locked for the expiry job)."""
    stmt = (
        select(g)
        .where(g.c.status.in_(("approved", "active")), g.c.expires_at <= now)
        .order_by(g.c.expires_at)
        .with_for_update(skip_locked=True)
    )
    return list(session.execute(stmt).mappings())


def list_stale_requests(session: Session, cutoff: dt.datetime) -> list[RowMapping]:
    """Requests the school did not answer before ``cutoff``."""
    stmt = (
        select(g)
        .where(
            g.c.status == "requested",
            func.coalesce(g.c.requested_at, g.c.created_at) <= cutoff,
        )
        .with_for_update(skip_locked=True)
    )
    return list(session.execute(stmt).mappings())


def list_emergency_without_membership(session: Session, now: dt.datetime) -> list[RowMapping]:
    stmt = select(g).where(
        g.c.emergency.is_(True),
        g.c.status == "approved",
        g.c.membership_id.is_(None),
        g.c.expires_at > now,
    )
    return list(session.execute(stmt).mappings())


def list_unreported(session: Session) -> list[RowMapping]:
    """Grants whose current outcome has not been reported to the control plane yet."""
    stmt = select(g.c.id, g.c.platform_request_id, g.c.status).where(
        g.c.platform_request_id.is_not(None),
        g.c.status.in_(PUSHABLE),
        or_(g.c.platform_status_synced.is_(None), g.c.platform_status_synced != g.c.status),
    )
    return list(session.execute(stmt).mappings())


def mark_reported(session: Session, grant_id: uuid.UUID, status: str) -> None:
    session.execute(
        update(g)
        .where(g.c.id == grant_id, g.c.status == status)
        .values(platform_status_synced=status)
    )
