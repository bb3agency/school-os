"""Database access for UDISE+ correction requests (FO-02; migration 0088_udise_corrections).

Bound parameters only; RLS limits every read and write to the current school. Private to
``app.dq`` (callers use :mod:`app.dq.udise_corrections`).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import RowMapping, func, insert, select, update
from sqlalchemy.orm import Session

from app.dq import models as m

Q = m.udise_correction_requests
L = m.udise_correction_lines
OPEN_STATUSES = ("prepared", "sent")


def insert_request(session: Session, values: Mapping[str, Any]) -> RowMapping:
    return session.execute(insert(Q).values(**values).returning(*Q.c)).mappings().one()


def insert_lines(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    if rows:
        session.execute(insert(L), list(rows))


def get_request(
    session: Session, request_id: uuid.UUID, *, lock: bool = False
) -> RowMapping | None:
    stmt = select(Q).where(Q.c.id == request_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).mappings().one_or_none()


def list_requests(session: Session, *, limit: int) -> list[RowMapping]:
    stmt = select(Q).order_by(Q.c.prepared_at.desc(), Q.c.id.desc()).limit(limit)
    return list(session.execute(stmt).mappings())


def update_request(
    session: Session, request_id: uuid.UUID, expected_version: int, values: Mapping[str, Any]
) -> RowMapping | None:
    """Update with optimistic locking; ``None`` when the version moved on."""
    stmt = (
        update(Q)
        .where(Q.c.id == request_id, Q.c.version == expected_version)
        .values(version=Q.c.version + 1, updated_at=func.now(), **values)
        .returning(*Q.c)
    )
    return session.execute(stmt).mappings().one_or_none()


def lines_of(session: Session, request_id: uuid.UUID) -> list[RowMapping]:
    stmt = select(L).where(L.c.request_id == request_id).order_by(L.c.created_at, L.c.id)
    return list(session.execute(stmt).mappings())


def open_lines(
    session: Session, student_ids: Collection[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], uuid.UUID]:
    """(student, field) -> the open request (prepared or sent) that already asks for it."""
    if not student_ids:
        return {}
    stmt = (
        select(L.c.student_id, L.c.attribute_key, L.c.request_id)
        .join(Q, (Q.c.tenant_id == L.c.tenant_id) & (Q.c.id == L.c.request_id))
        .where(L.c.student_id.in_(list(student_ids)), Q.c.status.in_(OPEN_STATUSES))
    )
    return {(r.student_id, r.attribute_key): r.request_id for r in session.execute(stmt)}


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


__all__ = [
    "OPEN_STATUSES",
    "get_request",
    "insert_lines",
    "insert_request",
    "lines_of",
    "list_requests",
    "now",
    "open_lines",
    "update_request",
]
