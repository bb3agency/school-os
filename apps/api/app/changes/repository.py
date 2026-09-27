"""Database access for change requests. Only ``app.changes.service`` calls this module.

Every function runs in the caller's ``tenant_session``: RLS limits each statement to the current
school. Object-level scope (which students the caller reaches) is decided by the service and
passed in as an explicit student-id filter.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import and_, false, func, insert, or_, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.changes.models import ChangeRequest
from app.core.errors import Conflict, DomainError, Forbidden, ValidationFailed

_FIELDS: dict[str, str] = {
    "change_requests_reason_length": "reason",
    "change_requests_note_length": "note",
    "change_requests_reject_needs_note": "reason",
    "change_requests_student_fk": "student_id",
    "change_requests_evidence_fk": "evidence_document_id",
    "change_requests_source_check": "target_source",
    "change_requests_key_format": "attribute_key",
}


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Map a PostgreSQL error to a domain error (never echoes values); ``None`` if unexpected."""
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None) or ""
    if constraint == "change_requests_no_self_approval":
        return Forbidden(
            "You submitted this request, so someone else must decide it.",
            code="self_approval_forbidden",
        )
    if constraint == "change_requests_one_pending":
        return Conflict(
            "A correction for this field is already waiting for approval. "
            "Open it, or cancel it before submitting a new one.",
            code="duplicate_pending_request",
        )
    if constraint == "change_requests_frozen":
        return Conflict("This request was already decided.", code="request_not_pending")
    if state in ("23503", "23514") and constraint in _FIELDS:
        return ValidationFailed(
            [{"field": _FIELDS[constraint], "code": "invalid", "message_key": "errors.invalid"}]
        )
    return None


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    """Transaction timestamp (consistent within one request)."""
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def insert_request(session: Session, **values: Any) -> ChangeRequest:
    return session.scalars(insert(ChangeRequest).values(**values).returning(ChangeRequest)).one()


def get_request(
    session: Session, request_id: uuid.UUID, *, lock: bool = False
) -> ChangeRequest | None:
    stmt = select(ChangeRequest).where(ChangeRequest.id == request_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def update_request(
    session: Session, request_id: uuid.UUID, values: Mapping[str, Any]
) -> ChangeRequest:
    """Update workflow columns and bump ``version`` (the ETag)."""
    stmt = (
        update(ChangeRequest)
        .where(ChangeRequest.id == request_id)
        .values(**values, version=ChangeRequest.version + 1)
        .returning(ChangeRequest)
        .execution_options(synchronize_session=False)
    )
    return session.scalars(stmt, execution_options={"populate_existing": True}).one()


def list_requests(
    session: Session,
    *,
    student_ids: Collection[uuid.UUID] | None,
    status: str | None,
    student_id: uuid.UUID | None,
    after: tuple[dt.datetime, uuid.UUID] | None,
    limit: int,
) -> Sequence[ChangeRequest]:
    """Newest first; ``student_ids`` limits the rows to the caller's reach (``None`` = all)."""
    stmt = select(ChangeRequest)
    if student_ids is not None:
        ids = sorted(student_ids)
        stmt = stmt.where(ChangeRequest.student_id.in_(ids) if ids else false())
    if status is not None:
        stmt = stmt.where(ChangeRequest.status == status)
    if student_id is not None:
        stmt = stmt.where(ChangeRequest.student_id == student_id)
    if after is not None:
        at, last_id = after
        stmt = stmt.where(
            or_(
                ChangeRequest.requested_at < at,
                and_(ChangeRequest.requested_at == at, ChangeRequest.id < last_id),
            )
        )
    stmt = stmt.order_by(ChangeRequest.requested_at.desc(), ChangeRequest.id.desc()).limit(limit)
    return session.scalars(stmt).all()


def lock_due_for_expiry(
    session: Session, at: dt.datetime, *, limit: int
) -> Sequence[ChangeRequest]:
    """Pending requests whose window has passed (skip rows another worker holds)."""
    stmt = (
        select(ChangeRequest)
        .where(ChangeRequest.status == "pending", ChangeRequest.expires_at <= at)
        .order_by(ChangeRequest.expires_at, ChangeRequest.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return session.scalars(stmt).all()
