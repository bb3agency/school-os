"""Database access for the privacy request register (0066_privacy_requests; ADR-0048).

Callers inside ``app.privacy`` only. Every function runs in a ``core.db.tenant_session``: RLS
limits each statement to that school. Notices, events, links and counters are insert-only for
the app role; requests are updated only on the columns the workflow changes.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import ColumnElement, Integer, and_, cast, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, Forbidden, PreconditionFailed
from app.core.ids import new_id
from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.privacy.models import (
    PrivacyNotice,
    PrivacyRequest,
    PrivacyRequestCounter,
    PrivacyRequestEvent,
    PrivacyRequestLink,
)

OPEN_STATUSES = ("received", "in_progress", "waiting_for_requester", "decided")


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("privacy repository used outside tenant_session")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Constraint names -> API errors (the service checks first; these close races)."""
    message = str(exc.orig) if exc.orig is not None else str(exc)
    if "privacy_requests_decided_by_other" in message or (
        "privacy_requests_released_by_other" in message
    ):
        return Forbidden(
            "Someone other than the person who logged this request must decide it.",
            code="self_approval_forbidden",
        )
    if "privacy_request_events_seq_key" in message:
        return PreconditionFailed("Someone else changed this request just now. Reload it.")
    if "privacy_requests_decided_verified" in message:
        return Conflict("Record how you checked who asked first.", code="not_verified")
    return None


# --- notices ----------------------------------------------------------------------------------


def insert_notice(session: Session, values: Mapping[str, Any]) -> PrivacyNotice:
    row = PrivacyNotice(**dict(values))
    session.add(row)
    session.flush()
    return row


def list_notices(session: Session) -> list[PrivacyNotice]:
    stmt = select(PrivacyNotice).order_by(
        PrivacyNotice.issued_on.desc(), PrivacyNotice.recorded_at.desc()
    )
    return list(session.scalars(stmt))


# --- counters ---------------------------------------------------------------------------------


def next_number(session: Session, year: int) -> int:
    """Allocate the next reference number for ``year`` (locks the counter row; gap-free)."""
    tenant_id = current_tenant_id(session)
    stmt = (
        pg_insert(PrivacyRequestCounter)
        .values(id=new_id(), tenant_id=tenant_id, year=year, last_no=1)
        .on_conflict_do_update(
            index_elements=["tenant_id", "year"],
            set_={
                "last_no": PrivacyRequestCounter.last_no + 1,
                "updated_at": func.now(),
            },
        )
        .returning(PrivacyRequestCounter.last_no)
    )
    value: int = session.execute(stmt).scalar_one()
    return value


# --- requests ---------------------------------------------------------------------------------


def insert_request(session: Session, values: Mapping[str, Any]) -> PrivacyRequest:
    row = PrivacyRequest(**dict(values))
    session.add(row)
    session.flush()
    return row


def get_request(
    session: Session, request_id: uuid.UUID, *, lock: bool = False
) -> PrivacyRequest | None:
    stmt = select(PrivacyRequest).where(PrivacyRequest.id == request_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def update_request(
    session: Session, row: PrivacyRequest, values: Mapping[str, Any]
) -> PrivacyRequest:
    """Apply ``values`` and bump ``version`` (the caller checked ``If-Match`` on the locked
    row)."""
    session.execute(
        update(PrivacyRequest)
        .where(PrivacyRequest.id == row.id)
        .values(**dict(values), version=PrivacyRequest.version + 1)
        .execution_options(synchronize_session=False)
    )
    session.flush()
    session.refresh(row)
    return row


def search_requests(
    session: Session,
    *,
    kinds: Collection[str] | None,
    statuses: Collection[str] | None,
    overdue_before: dt.date | None,
    student_ids: Collection[uuid.UUID] | None,
    guardian_ids: Collection[uuid.UUID] | None,
    membership_id: uuid.UUID | None,
    reach_students: Collection[uuid.UUID] | None,
    reach_guardians: Collection[uuid.UUID] | None,
    after: tuple[int, dt.date, str] | None,
    limit: int,
) -> list[PrivacyRequest]:
    """Requests matching the filters, open ones first, then by due date and reference.

    ``reach_students`` / ``reach_guardians`` (``None``: the whole school): requests about
    students outside them and guardians not linked to them are left out (staff subjects stay
    visible). Sort key: (0 open / 1 closed, due_on, reference)."""
    is_closed = cast(PrivacyRequest.status.in_(("closed", "withdrawn")), Integer)
    stmt = select(PrivacyRequest)
    if kinds:
        stmt = stmt.where(PrivacyRequest.kind.in_(list(kinds)))
    if statuses:
        stmt = stmt.where(PrivacyRequest.status.in_(list(statuses)))
    if overdue_before is not None:
        stmt = stmt.where(
            PrivacyRequest.status.in_(OPEN_STATUSES), PrivacyRequest.due_on < overdue_before
        )
    subject_filters: list[ColumnElement[bool]] = []
    if student_ids is not None:
        subject_filters.append(PrivacyRequest.student_id.in_(list(student_ids)))
    if guardian_ids is not None:
        subject_filters.append(PrivacyRequest.guardian_id.in_(list(guardian_ids)))
    if membership_id is not None:
        subject_filters.append(PrivacyRequest.membership_id == membership_id)
    if subject_filters:
        stmt = stmt.where(or_(*subject_filters))
    if reach_students is not None:
        stmt = stmt.where(
            or_(
                PrivacyRequest.subject_kind == "member",
                and_(
                    PrivacyRequest.subject_kind == "student",
                    PrivacyRequest.student_id.in_(list(reach_students)),
                ),
                and_(
                    PrivacyRequest.subject_kind == "guardian",
                    PrivacyRequest.guardian_id.in_(list(reach_guardians or ())),
                ),
            )
        )
    if after is not None:
        a_closed, a_due, a_ref = after
        stmt = stmt.where(
            or_(
                is_closed > a_closed,
                and_(is_closed == a_closed, PrivacyRequest.due_on > a_due),
                and_(
                    is_closed == a_closed,
                    PrivacyRequest.due_on == a_due,
                    PrivacyRequest.reference > a_ref,
                ),
            )
        )
    stmt = stmt.order_by(is_closed, PrivacyRequest.due_on, PrivacyRequest.reference).limit(limit)
    return list(session.scalars(stmt))


def due_open_requests(session: Session, until: dt.date) -> list[PrivacyRequest]:
    """Open requests due on or before ``until`` (the daily reminder)."""
    stmt = (
        select(PrivacyRequest)
        .where(PrivacyRequest.status.in_(OPEN_STATUSES), PrivacyRequest.due_on <= until)
        .order_by(PrivacyRequest.due_on, PrivacyRequest.reference)
    )
    return list(session.scalars(stmt))


def requests_about(
    session: Session,
    *,
    student_id: uuid.UUID | None = None,
    guardian_id: uuid.UUID | None = None,
    membership_id: uuid.UUID | None = None,
) -> list[PrivacyRequest]:
    stmt = select(PrivacyRequest)
    if student_id is not None:
        stmt = stmt.where(PrivacyRequest.student_id == student_id)
    elif guardian_id is not None:
        stmt = stmt.where(PrivacyRequest.guardian_id == guardian_id)
    elif membership_id is not None:
        stmt = stmt.where(PrivacyRequest.membership_id == membership_id)
    else:
        return []
    return list(session.scalars(stmt.order_by(PrivacyRequest.received_on.desc())))


# --- events and links -------------------------------------------------------------------------


def events_of(session: Session, request_id: uuid.UUID) -> list[PrivacyRequestEvent]:
    stmt = (
        select(PrivacyRequestEvent)
        .where(PrivacyRequestEvent.request_id == request_id)
        .order_by(PrivacyRequestEvent.seq)
    )
    return list(session.scalars(stmt))


def add_event(
    session: Session,
    request_id: uuid.UUID,
    event: str,
    detail: Mapping[str, Any],
    *,
    actor_user_id: uuid.UUID | None,
    actor_membership_id: uuid.UUID | None,
) -> PrivacyRequestEvent:
    last: int | None = session.execute(
        select(func.max(PrivacyRequestEvent.seq)).where(
            PrivacyRequestEvent.request_id == request_id
        )
    ).scalar_one()
    row = PrivacyRequestEvent(
        id=new_id(),
        tenant_id=current_tenant_id(session),
        request_id=request_id,
        seq=(last or 0) + 1,
        event=event,
        detail=json.loads(json.dumps(dict(detail), default=str)),
        actor_user_id=actor_user_id,
        actor_membership_id=actor_membership_id,
    )
    session.add(row)
    session.flush()
    return row


def links_of(session: Session, request_id: uuid.UUID) -> list[PrivacyRequestLink]:
    stmt = (
        select(PrivacyRequestLink)
        .where(PrivacyRequestLink.request_id == request_id)
        .order_by(PrivacyRequestLink.created_at)
    )
    return list(session.scalars(stmt))


def add_link(
    session: Session,
    request_id: uuid.UUID,
    link_kind: str,
    target_id: uuid.UUID,
    *,
    created_by: uuid.UUID,
) -> bool:
    """Insert the link; ``False`` when it already exists."""
    stmt = (
        pg_insert(PrivacyRequestLink)
        .values(
            id=new_id(),
            tenant_id=current_tenant_id(session),
            request_id=request_id,
            link_kind=link_kind,
            target_id=target_id,
            created_by=created_by,
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "request_id", "link_kind", "target_id"]
        )
        .returning(PrivacyRequestLink.id)
    )
    return session.execute(stmt).first() is not None


# --- export (FR-ADM-001, FR-DPDP-018) ---------------------------------------------------------

CIPHERTEXT_COLUMNS = (
    "requester_name_ciphertext",
    "request_ciphertext",
    "notes_ciphertext",
    "response_ciphertext",
    "nominee_ciphertext",
)


def export_tables(session: Session) -> list[RecordTable]:
    """The register for the school's full data export (ciphertext left out)."""
    return [
        dump_table(session, PrivacyNotice.__table__, name="privacy_notices"),
        dump_table(
            session,
            PrivacyRequest.__table__,
            name="privacy_requests",
            exclude=CIPHERTEXT_COLUMNS,
            order_by=("year", "number"),
        ),
        dump_table(
            session,
            PrivacyRequestEvent.__table__,
            name="privacy_request_events",
            order_by=("request_id", "seq"),
        ),
        dump_table(session, PrivacyRequestLink.__table__, name="privacy_request_links"),
    ]


def set_texts(session: Session, request_id: uuid.UUID, values: Mapping[str, bytes]) -> None:
    """Key rotation only: replace ciphertext in place (no version bump)."""
    session.execute(
        update(PrivacyRequest)
        .where(PrivacyRequest.id == request_id)
        .values(**dict(values))
        .execution_options(synchronize_session=False)
    )


def all_requests(session: Session) -> list[PrivacyRequest]:
    stmt = select(PrivacyRequest).order_by(PrivacyRequest.year, PrivacyRequest.number)
    return list(session.scalars(stmt))
