"""Database access for the TC request log (FO-09; ``sis.tc_requests``, migration
0102_tc_requests; FR-CERT-026..FR-CERT-045). Only ``app.certificates`` calls this module.

Every function runs in the caller's ``tenant_session``: RLS limits each statement to the current
school. Which students the caller reaches is decided by the service and passed in.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import and_, false, func, insert, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.certificates.models import TC_REQUEST_OPEN, TcRequest
from app.core.errors import Conflict, DomainError
from app.core.record_tables import dump_table
from app.core.records import RecordTable

T = TcRequest


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """``tc_requests_one_open`` -> 409 ``tc_request_open_exists``; ``None`` if unexpected."""
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or ""
    if constraint == "tc_requests_one_open":
        return Conflict(
            "This student already has an open TC request. Open it instead of logging a new one.",
            code="tc_request_open_exists",
        )
    return None


def insert_request(session: Session, **values: Any) -> TcRequest:
    return session.scalars(insert(T).values(**values).returning(T)).one()


def get_request(session: Session, request_id: uuid.UUID, *, lock: bool = False) -> TcRequest | None:
    stmt = select(T).where(T.id == request_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def update_request(session: Session, request_id: uuid.UUID, values: Mapping[str, Any]) -> TcRequest:
    stmt = (
        update(T)
        .where(T.id == request_id)
        .values(**values, version=T.version + 1)
        .returning(T)
        .execution_options(synchronize_session=False)
    )
    return session.scalars(stmt, execution_options={"populate_existing": True}).one()


def open_request_of(
    session: Session, student_id: uuid.UUID, *, lock: bool = False
) -> TcRequest | None:
    """The student's open request (received, in preparation or ready); at most one exists."""
    stmt = select(T).where(T.student_id == student_id, T.status.in_(TC_REQUEST_OPEN))
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def request_of_certificate(
    session: Session, certificate_id: uuid.UUID, *, lock: bool = False
) -> TcRequest | None:
    """The open request a transfer certificate was prepared for, if any."""
    stmt = select(T).where(T.certificate_id == certificate_id, T.status.in_(TC_REQUEST_OPEN))
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def list_requests(
    session: Session,
    *,
    student_ids: Collection[uuid.UUID] | None,
    statuses: Collection[str] | None = None,
    received_before: dt.date | None = None,
    after: tuple[dt.date, uuid.UUID] | None = None,
    limit: int = 50,
) -> list[TcRequest]:
    """Newest received first; ``student_ids`` ``None`` = the whole school. ``received_before``
    keeps requests received before that day (the overdue filter)."""
    stmt = select(T)
    if student_ids is not None:
        ids = list(student_ids)
        stmt = stmt.where(T.student_id.in_(ids) if ids else false())
    if statuses is not None:
        stmt = stmt.where(T.status.in_(list(statuses)))
    if received_before is not None:
        stmt = stmt.where(T.received_on < received_before)
    if after is not None:
        day, last = after
        stmt = stmt.where(or_(T.received_on < day, and_(T.received_on == day, T.id < last)))
    stmt = stmt.order_by(T.received_on.desc(), T.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def count_overdue(
    session: Session, *, student_ids: Collection[uuid.UUID] | None, received_before: dt.date
) -> int:
    """Requests still received or in preparation that were received before ``received_before``."""
    stmt = select(func.count()).select_from(T)
    stmt = stmt.where(T.status.in_(("received", "in_preparation")), T.received_on < received_before)
    if student_ids is not None:
        ids = list(student_ids)
        stmt = stmt.where(T.student_id.in_(ids) if ids else false())
    return int(session.execute(stmt).scalar_one())


def requests_of_student(session: Session, student_id: uuid.UUID, *, limit: int) -> list[TcRequest]:
    stmt = (
        select(T)
        .where(T.student_id == student_id)
        .order_by(T.received_on.desc(), T.id.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def export_table(session: Session) -> RecordTable:
    """The request log of the current school for its full data export (FR-ADM-001)."""
    return dump_table(session, T.__table__, name="tc_requests", order_by=("received_on", "id"))


__all__ = [
    "count_overdue",
    "export_table",
    "get_request",
    "insert_request",
    "list_requests",
    "open_request_of",
    "request_of_certificate",
    "requests_of_student",
    "translate_db_error",
    "update_request",
]
