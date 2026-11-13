"""The TC request log follows the transfer certificate (FO-09; FR-CERT-031..FR-CERT-033,
FR-CERT-039..FR-CERT-041).

Called by ``app.certificates.service`` inside the certificate's own transaction, so the request
moves with the certificate or not at all (invariant 7: audited in the same transaction):

- a TC prepared for a student with a request ``received``: the request is linked and becomes
  ``in_preparation``;
- the TC issued (approved): the request becomes ``ready`` (to collect);
- the TC rejected or withdrawn, or cancelled after issue: the request is unlinked and goes back
  to ``in_preparation`` (the office prepares it again).

It also gives the TC preview its "From SchoolOS" values from the open request (the day received
is the application date; the leaving date and reason the parent asked for) and from the latest
issued conduct certificate. Audit summaries hold IDs and statuses only, never names.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.certificates import tc_requests_repository as repo
from app.certificates.models import Certificate, TcRequest
from app.core.logging import get_context

STATUS_ACTION: Final = "tc_request.status_changed"


def record_change(
    session: Session,
    row: TcRequest,
    *,
    previous: str,
    linked: uuid.UUID | None,
    automatic: bool,
) -> None:
    """Audit one change of a request (same transaction; IDs and statuses only)."""
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action=STATUS_ACTION,
        resource_type="tc_request",
        resource_id=row.id,
        summary={
            "student_id": row.student_id,
            "from": previous,
            "to": row.status,
            "certificate_id": linked,
            "automatic": automatic,
        },
        actor_type="user",
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _move(
    session: Session, row: TcRequest, values: dict[str, Any], *, linked: uuid.UUID | None
) -> TcRequest:
    previous = row.status
    moved = repo.update_request(session, row.id, values)
    record_change(session, moved, previous=previous, linked=linked, automatic=True)
    return moved


def on_tc_prepared(
    session: Session,
    *,
    student_id: uuid.UUID,
    certificate_id: uuid.UUID,
    membership_id: uuid.UUID,
    at: dt.datetime,
) -> None:
    """A TC was prepared (waiting for approval): link the student's open request."""
    row = repo.open_request_of(session, student_id, lock=True)
    if row is None or row.status == "ready":
        return
    values: dict[str, Any] = {"certificate_id": certificate_id, "status": "in_preparation"}
    if row.preparing_at is None:
        values.update(preparing_by=membership_id, preparing_at=at)
    _move(session, row, values, linked=certificate_id)


def on_tc_issued(
    session: Session,
    *,
    student_id: uuid.UUID,
    certificate_id: uuid.UUID,
    membership_id: uuid.UUID,
    at: dt.datetime,
) -> None:
    """The TC was issued (serial number given): its request is ready to collect."""
    row = repo.request_of_certificate(session, certificate_id, lock=True)
    if row is None:
        row = repo.open_request_of(session, student_id, lock=True)
    if row is None or row.status == "ready":
        return
    values: dict[str, Any] = {
        "certificate_id": certificate_id,
        "status": "ready",
        "ready_by": membership_id,
        "ready_at": at,
    }
    if row.preparing_at is None:
        values.update(preparing_by=membership_id, preparing_at=at)
    _move(session, row, values, linked=certificate_id)


def on_tc_dropped(session: Session, *, certificate_id: uuid.UUID) -> None:
    """The TC was rejected, withdrawn or cancelled: the request is prepared again."""
    row = repo.request_of_certificate(session, certificate_id, lock=True)
    if row is None:
        return
    _move(
        session,
        row,
        {"certificate_id": None, "status": "in_preparation", "ready_by": None, "ready_at": None},
        linked=None,
    )


def suggestions(
    session: Session, student_id: uuid.UUID, today: dt.date
) -> list[tuple[str, str, str]]:
    """(input key, value, source) the TC preview may suggest: from the student's open request
    (application date = the day received; the leaving date unless it is still ahead; the
    reason) and the conduct of the latest issued conduct certificate."""
    out: list[tuple[str, str, str]] = []
    row = repo.open_request_of(session, student_id)
    if row is not None:
        out.append(("application_date", row.received_on.isoformat(), "tc_request"))
        if row.leaving_date is not None and row.leaving_date <= today:
            out.append(("leaving_date", row.leaving_date.isoformat(), "tc_request"))
        if row.leaving_reason is not None:
            out.append(("leaving_reason", row.leaving_reason, "tc_request"))
    conduct = session.execute(
        select(Certificate.inputs)
        .where(
            Certificate.student_id == student_id,
            Certificate.certificate_type == "conduct",
            Certificate.original_certificate_id.is_(None),
            Certificate.status == "issued",
        )
        .order_by(Certificate.issued_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    found: dict[str, Any] = conduct if isinstance(conduct, dict) else {}
    value = found.get("conduct")
    if isinstance(value, str) and value:
        out.append(("conduct", value, "conduct_certificate"))
    return out


__all__ = ["STATUS_ACTION", "on_tc_dropped", "on_tc_issued", "on_tc_prepared", "suggestions"]
