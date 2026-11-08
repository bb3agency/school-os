"""Database access for ``ops`` (tenant_session, RLS applies; bound parameters only)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Literal

from sqlalchemy import RowMapping, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.ops import models as m


def current_tenant(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


def insert_outbox(
    session: Session, tenant_id: uuid.UUID, event_type: str, payload: Mapping[str, Any]
) -> uuid.UUID:
    event_id = new_id()
    session.execute(
        m.outbox.insert().values(
            id=event_id, tenant_id=tenant_id, event_type=event_type, payload=dict(payload)
        )
    )
    return event_id


def purge_delivered_outbox(session: Session, *, older_than_days: int) -> int:
    """Delete the current school's events dispatched more than ``older_than_days`` ago (D51).
    Undelivered events (``dispatched_at IS NULL``) are never touched; the database allows
    ``sos_app`` to delete dispatched rows only (policy ``outbox_delete_dispatched``, 0072)."""
    o = m.outbox
    result = session.execute(
        delete(o)
        .where(
            o.c.tenant_id == func.core.current_tenant(),
            o.c.dispatched_at.is_not(None),
            o.c.dispatched_at < func.now() - func.make_interval(0, 0, 0, older_than_days),
        )
        .returning(o.c.id)
    )
    return len(result.all())


def claim_consumer(
    session: Session, event_id: uuid.UUID
) -> Literal["claimed", "consumed", "missing"]:
    """Stamp ``consumed_at`` on the current school's event unless it is already stamped (audit
    YM-06). ``missing``: no such event (never written, or purged after delivery)."""
    o = m.outbox
    claimed = session.execute(
        update(o)
        .where(o.c.id == event_id, o.c.consumed_at.is_(None))
        .values(consumed_at=func.now())
        .returning(o.c.id)
    ).first()
    if claimed is not None:
        return "claimed"
    exists = session.execute(select(o.c.id).where(o.c.id == event_id)).first()
    return "consumed" if exists is not None else "missing"


def release_consumer(session: Session, event_id: uuid.UUID) -> None:
    o = m.outbox
    session.execute(update(o).where(o.c.id == event_id).values(consumed_at=None))


def claim_outbox(session: Session, batch: int) -> list[RowMapping]:
    return list(
        session.execute(
            text("SELECT id, tenant_id, event_type, payload FROM ops.claim_outbox(:b)"),
            {"b": batch},
        ).mappings()
    )


def start_job(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    task_name: str,
    idempotency_key: str,
    created_by: uuid.UUID | None,
) -> tuple[RowMapping, bool]:
    row = (
        session.execute(
            pg_insert(m.job_runs)
            .values(
                id=new_id(),
                tenant_id=tenant_id,
                task_name=task_name,
                idempotency_key=idempotency_key,
                status="running",
                attempts=1,
                created_by=created_by,
                started_at=func.now(),
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
            .returning(m.job_runs)
        )
        .mappings()
        .first()
    )
    if row is not None:
        return row, True
    existing = (
        session.execute(
            select(m.job_runs)
            .where(
                m.job_runs.c.tenant_id == tenant_id, m.job_runs.c.idempotency_key == idempotency_key
            )
            .with_for_update()
        )
        .mappings()
        .one()
    )
    return existing, False


def update_job(session: Session, job_id: uuid.UUID, values: Mapping[str, Any]) -> RowMapping:
    return (
        session.execute(
            update(m.job_runs)
            .where(m.job_runs.c.id == job_id)
            .values(**values)
            .returning(m.job_runs)
        )
        .mappings()
        .one()
    )


def get_job(session: Session, job_id: uuid.UUID) -> RowMapping | None:
    return (
        session.execute(select(m.job_runs).where(m.job_runs.c.id == job_id))
        .mappings()
        .one_or_none()
    )


def idem_claim(session: Session, values: Mapping[str, Any]) -> bool:
    row = session.execute(
        pg_insert(m.idempotency_keys)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["tenant_id", "user_id", "key"])
        .returning(m.idempotency_keys.c.key)
    ).first()
    return row is not None


def idem_get(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, key: str
) -> RowMapping | None:
    return (
        session.execute(
            select(m.idempotency_keys)
            .where(
                m.idempotency_keys.c.tenant_id == tenant_id,
                m.idempotency_keys.c.user_id == user_id,
                m.idempotency_keys.c.key == key,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )


def idem_update(
    session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, key: str, values: Mapping[str, Any]
) -> None:
    session.execute(
        update(m.idempotency_keys)
        .where(
            m.idempotency_keys.c.tenant_id == tenant_id,
            m.idempotency_keys.c.user_id == user_id,
            m.idempotency_keys.c.key == key,
        )
        .values(**values)
    )


def idem_delete(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, key: str) -> None:
    session.execute(
        delete(m.idempotency_keys).where(
            m.idempotency_keys.c.tenant_id == tenant_id,
            m.idempotency_keys.c.user_id == user_id,
            m.idempotency_keys.c.key == key,
        )
    )


def idem_purge_expired(session: Session) -> int:
    result = session.execute(
        delete(m.idempotency_keys)
        .where(m.idempotency_keys.c.expires_at < func.now())
        .returning(m.idempotency_keys.c.key)
    )
    return len(result.all())
