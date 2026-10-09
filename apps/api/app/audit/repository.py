"""Database access for audit events and chain heads. Bound parameters only."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import RowMapping, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.audit.models import (
    chain_heads,
    chain_verifications,
    events,
    platform_chain_head,
    platform_events,
)
from app.audit.schemas import ZERO_HASH

STREAM_BATCH = 1000


def current_tenant(session: Session) -> uuid.UUID | None:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    return None if value is None else uuid.UUID(str(value))


def current_user_id(session: Session) -> uuid.UUID | None:
    value: object = session.execute(text("SELECT core.current_user_id()")).scalar_one()
    return None if value is None else uuid.UUID(str(value))


# ---- tenant chain ----


def lock_head(session: Session, tenant_id: uuid.UUID) -> tuple[int, bytes]:
    """Lock the tenant's chain head (creating the genesis head on first use)."""
    stmt = (
        select(chain_heads.c.last_seq, chain_heads.c.last_hash)
        .where(chain_heads.c.tenant_id == tenant_id)
        .with_for_update()
    )
    row = session.execute(stmt).one_or_none()
    if row is None:
        session.execute(
            pg_insert(chain_heads)
            .values(tenant_id=tenant_id, last_seq=0, last_hash=ZERO_HASH, updated_at=func.now())
            .on_conflict_do_nothing(index_elements=[chain_heads.c.tenant_id])
        )
        row = session.execute(stmt).one()
    return int(row.last_seq), bytes(row.last_hash)


def insert_event(session: Session, values: Mapping[str, Any]) -> None:
    session.execute(events.insert().values(**values))


def advance_head(session: Session, tenant_id: uuid.UUID, seq: int, event_hash: bytes) -> None:
    session.execute(
        update(chain_heads)
        .where(chain_heads.c.tenant_id == tenant_id)
        .values(last_seq=seq, last_hash=event_hash, updated_at=func.now())
    )


def get_head(session: Session, tenant_id: uuid.UUID) -> tuple[int, bytes] | None:
    row = session.execute(
        select(chain_heads.c.last_seq, chain_heads.c.last_hash).where(
            chain_heads.c.tenant_id == tenant_id
        )
    ).one_or_none()
    return None if row is None else (int(row.last_seq), bytes(row.last_hash))


def iter_events(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[RowMapping]:
    """Stream a tenant's events in chain order (optionally within [start, end))."""
    stmt = select(events).where(events.c.tenant_id == tenant_id)
    if start is not None:
        stmt = stmt.where(events.c.occurred_at >= start)
    if end is not None:
        stmt = stmt.where(events.c.occurred_at < end)
    stmt = stmt.order_by(events.c.seq, events.c.occurred_at, events.c.id)
    result = session.execute(stmt.execution_options(yield_per=STREAM_BATCH))
    yield from result.mappings()


def event_days(
    session: Session, tenant_id: uuid.UUID, *, start: datetime, end: datetime
) -> list[date]:
    """The UTC days in ``[start, end)`` on which the tenant has events (archive backfill)."""
    day = func.date(func.timezone("UTC", events.c.occurred_at))
    stmt = (
        select(day)
        .where(
            events.c.tenant_id == tenant_id,
            events.c.occurred_at >= start,
            events.c.occurred_at < end,
        )
        .distinct()
    )
    return list(session.execute(stmt).scalars())


def events_at(session: Session, tenant_id: uuid.UUID, seq: int) -> list[RowMapping]:
    """Every row with this ``seq`` (normally one; more is a duplicated sequence)."""
    stmt = select(events).where(events.c.tenant_id == tenant_id, events.c.seq == seq)
    return list(session.execute(stmt).mappings())


def iter_events_after(session: Session, tenant_id: uuid.UUID, seq: int) -> Iterator[RowMapping]:
    """Stream a tenant's events after ``seq`` in chain order (incremental verification)."""
    stmt = (
        select(events)
        .where(events.c.tenant_id == tenant_id, events.c.seq > seq)
        .order_by(events.c.seq, events.c.occurred_at, events.c.id)
    )
    result = session.execute(stmt.execution_options(yield_per=STREAM_BATCH))
    yield from result.mappings()


# ---- stored verification results (R-19) ----


def get_verification(
    session: Session, tenant_id: uuid.UUID, *, for_update: bool = False
) -> RowMapping | None:
    stmt = select(chain_verifications).where(chain_verifications.c.tenant_id == tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    return session.execute(stmt).mappings().one_or_none()


def save_verification(session: Session, tenant_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    """Insert or update the school's verification row."""
    stmt = pg_insert(chain_verifications).values(
        tenant_id=tenant_id, updated_at=func.now(), **values
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[chain_verifications.c.tenant_id],
            set_={**values, "updated_at": func.now()},
        )
    )


# ---- platform chain ----


def lock_platform_head(session: Session) -> tuple[int, bytes]:
    row = session.execute(
        select(platform_chain_head.c.last_seq, platform_chain_head.c.last_hash)
        .where(platform_chain_head.c.id.is_(True))
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise RuntimeError("platform.audit_chain_head is missing; run migration 0002_audit")
    return int(row.last_seq), bytes(row.last_hash)


def insert_platform_event(session: Session, values: Mapping[str, Any]) -> None:
    session.execute(platform_events.insert().values(**values))


def advance_platform_head(session: Session, seq: int, event_hash: bytes) -> None:
    session.execute(
        update(platform_chain_head)
        .where(platform_chain_head.c.id.is_(True))
        .values(last_seq=seq, last_hash=event_hash, updated_at=func.now())
    )


def get_platform_head(session: Session) -> tuple[int, bytes] | None:
    row = session.execute(
        select(platform_chain_head.c.last_seq, platform_chain_head.c.last_hash).where(
            platform_chain_head.c.id.is_(True)
        )
    ).one_or_none()
    return None if row is None else (int(row.last_seq), bytes(row.last_hash))


def iter_platform_events(session: Session) -> Iterator[RowMapping]:
    stmt = select(platform_events).order_by(platform_events.c.seq, platform_events.c.id)
    result = session.execute(stmt.execution_options(yield_per=STREAM_BATCH))
    yield from result.mappings()


# ---- partitions ----


def partition_names(session: Session) -> list[str]:
    """Names of the monthly partitions of audit.events (catalog read; no table privileges)."""
    rows: Iterable[object] = session.execute(
        text(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "JOIN pg_namespace n ON n.oid = p.relnamespace "
            "WHERE n.nspname = 'audit' AND p.relname = 'events'"
        )
    ).scalars()
    return sorted(str(r) for r in rows)
