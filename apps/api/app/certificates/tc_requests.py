"""TC request log (FO-09; FR-CERT-026..FR-CERT-045; US-1101, BO-04). *(Lead decision D86, PO
to confirm.)*

Transfer certificate requests reach the office on paper, by phone or in person; staff log them
here (there is no parent-facing page: a public page that writes would need an ADR-0046
amendment). Each request keeps its steps with who and when:

    received -> in_preparation -> ready -> issued (handed over)
    received | in_preparation | ready -> cancelled (with a reason)

- **Follows the TC (FR-CERT-031..FR-CERT-033).** Preparing a TC for the student moves the open
  request to ``in_preparation``; issuing it (the principal's approval) to ``ready``; a rejected,
  withdrawn or cancelled TC sends it back to ``in_preparation`` (``tc_request_hooks``).
- **Prefill (FR-CERT-028, FR-CERT-029).** The TC preview suggests the day received as the
  application date and the leaving date and reason the parent asked for. Nothing is filled in
  silently; the clerk accepts or types each value.
- **Overdue (FR-CERT-036, FR-CERT-037).** A request still received or in preparation more than
  ``tc_requests.overdue_after_days`` (config, default 7, ``verified: false``) after it was
  received is overdue; the list shows it and counts it.
- **Dues (FR-CERT-038).** The fee note of the TC preview (FR-CERT-025) is shown on the request
  to holders of ``finance.read``; it is information only. A TC is never withheld or delayed for
  dues (owner decision D16, ADR-0042 decision 13): there is no setting that blocks.
- **Permissions.** No new ones: reading needs ``certificate.read``, ``.issue`` or ``.approve``;
  logging and moving a request need ``certificate.issue``. Students outside the caller's scope
  are 404 (SEC-015).
- Audit (``tc_request.logged``, ``tc_request.status_changed``) in the same transaction, IDs and
  statuses only. Logs carry IDs only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Final

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.authz.http import decode_cursor, encode_cursor
from app.certificates import repository as cert_repo
from app.certificates import service as certificates
from app.certificates import tc_request_hooks as hooks
from app.certificates import tc_requests_repository as repo
from app.certificates.config import TcRequestsSpec
from app.certificates.models import Certificate, TcRequest
from app.certificates.schemas import (
    TcRequestIn,
    TcRequestList,
    TcRequestOut,
    TcRequestStatus,
    TcRequestStatusIn,
)
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.identity import service as identity
from app.students import service as students

LOGGED_ACTION: Final = "tc_request.logged"
WAITING: Final = ("received", "in_preparation")
_READERS: Final = (certificates.READ, certificates.ISSUE, certificates.APPROVE)
# Allowed manual moves: target -> statuses it may come from (ready is set by the TC's issue).
_MOVES: Final[Mapping[str, tuple[str, ...]]] = {
    "in_preparation": ("received",),
    "issued": ("ready",),
    "cancelled": ("received", "in_preparation", "ready"),
}

log = get_logger(__name__)


class TcRequestWrongStatus(Conflict):
    code = "tc_request_wrong_status"

    def __init__(self) -> None:
        super().__init__("This request is not at a step where that can be done. Reload it.")


class TcRequestCertificatePending(Conflict):
    code = "tc_request_certificate_pending"

    def __init__(self) -> None:
        super().__init__(
            "A TC for this request is waiting for approval. Withdraw it or ask the principal to "
            "reject it first, then cancel the request."
        )


class TcRequestCertificateNotIssued(Conflict):
    code = "tc_request_certificate_not_issued"

    def __init__(self) -> None:
        super().__init__(
            "The TC for this request is no longer issued. Open the certificate to see why."
        )


def settings() -> TcRequestsSpec:
    return certificates.settings().tc_requests


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _ist_date(value: dt.datetime) -> dt.date:
    return value.astimezone(certificates.IST).date()


def _overdue_before(today: dt.date) -> dt.date:
    """Requests received before this day and still waiting are overdue."""
    return today - dt.timedelta(days=settings().overdue_after_days)


def _days_open(row: TcRequest, today: dt.date) -> int:
    if row.ready_at is not None:
        end = _ist_date(row.ready_at)
    elif row.cancelled_at is not None:
        end = _ist_date(row.cancelled_at)
    else:
        end = today
    return max(0, (end - row.received_on).days)


def _load(
    session: Session,
    ctx: UserContext,
    request_id: uuid.UUID,
    *,
    permissions: Sequence[str],
    lock: bool = False,
) -> TcRequest:
    row = repo.get_request(session, request_id, lock=lock)
    if row is None or not certificates._in_reach(
        certificates._reach(session, ctx, permissions), row.student_id
    ):
        raise NotFound("TC request not found")
    return row


def _class_labels(session: Session, student_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not student_ids:
        return {}
    structure = certificates._Structure.load(session)
    out: dict[uuid.UUID, str] = {}
    for sid, enrolments in students.enrolment_histories(session, student_ids).items():
        live = [e for e in enrolments if e.status == certificates.LIVE_ENROLMENT]
        chosen = live or enrolments
        if chosen:
            out[sid] = structure.class_labels(chosen[-1].section_id)[0]
    return out


def _outs(
    session: Session,
    ctx: UserContext,
    rows: Sequence[TcRequest],
    *,
    with_fee_note: bool = False,
) -> list[TcRequestOut]:
    if not rows:
        return []
    today = certificates._today(session)
    overdue_before = _overdue_before(today)
    sids = {r.student_id for r in rows}
    values = students.canonical_values(session, sids, ["full_name", "admission_no"])
    classes = _class_labels(session, sids)
    linked = cert_repo.get_many(session, [r.certificate_id for r in rows if r.certificate_id])
    people = {
        m
        for r in rows
        for m in (r.received_by, r.preparing_by, r.ready_by, r.issued_by, r.cancelled_by)
        if m is not None
    }
    names = identity.member_display_names(session, people)
    may_issue = ctx.has(certificates.ISSUE)
    out: list[TcRequestOut] = []
    for r in rows:
        per = values.get(r.student_id, {})
        name, adm = per.get("full_name"), per.get("admission_no")
        cert: Certificate | None = linked.get(r.certificate_id) if r.certificate_id else None
        serial = cert.serial if cert is not None else None
        pending_cert = cert is not None and cert.status == "pending"
        out.append(
            TcRequestOut(
                id=r.id,
                student_id=r.student_id,
                student_name=name.value if name else None,
                admission_no=adm.value if adm else None,
                class_label=classes.get(r.student_id),
                status=r.status,
                received_on=r.received_on,
                channel=r.channel,
                leaving_date=r.leaving_date,
                leaving_reason=r.leaving_reason,
                certificate_id=r.certificate_id,
                certificate_status=cert.status if cert is not None else None,
                certificate_serial=serial,
                received_by=r.received_by,
                received_by_name=names.get(r.received_by),
                received_at=r.received_at,
                preparing_by=r.preparing_by,
                preparing_by_name=names.get(r.preparing_by) if r.preparing_by else None,
                preparing_at=r.preparing_at,
                ready_by=r.ready_by,
                ready_by_name=names.get(r.ready_by) if r.ready_by else None,
                ready_at=r.ready_at,
                issued_by=r.issued_by,
                issued_by_name=names.get(r.issued_by) if r.issued_by else None,
                issued_at=r.issued_at,
                cancelled_by=r.cancelled_by,
                cancelled_by_name=names.get(r.cancelled_by) if r.cancelled_by else None,
                cancelled_at=r.cancelled_at,
                cancel_reason=r.cancel_reason,
                days_open=_days_open(r, today),
                overdue=r.status in WAITING and r.received_on < overdue_before,
                fee_note=certificates._fee_note(session, ctx, r.student_id)
                if with_fee_note
                else None,
                can_prepare=may_issue and r.status == "received",
                can_hand_over=may_issue
                and r.status == "ready"
                and cert is not None
                and cert.status == "issued",
                can_cancel=may_issue and r.status in _MOVES["cancelled"] and not pending_cert,
                version=r.version,
            )
        )
    return out


def _after(cursor: str | None) -> tuple[dt.date, uuid.UUID] | None:
    raw = decode_cursor(cursor)
    if raw is None:
        return None
    try:
        return dt.date.fromisoformat(str(raw["d"])), uuid.UUID(str(raw["i"]))
    except (KeyError, ValueError):
        raise ValidationFailed([_error("cursor", "invalid_cursor")]) from None


def list_requests(
    session: Session,
    ctx: UserContext,
    *,
    status: TcRequestStatus | None = None,
    overdue: bool = False,
    student_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> TcRequestList:
    """The request log of students the caller reaches, newest received first (FR-CERT-035;
    read, issue or approve permission). ``overdue`` keeps only overdue requests."""
    reach = certificates._reach(session, ctx, _READERS)
    if student_id is not None:
        reach = (
            frozenset({student_id}) if certificates._in_reach(reach, student_id) else frozenset()
        )
    today = certificates._today(session)
    before = _overdue_before(today)
    statuses: tuple[str, ...] | None = (status,) if status is not None else None
    if overdue:
        statuses = tuple(s for s in WAITING if statuses is None or s in statuses)
    rows = repo.list_requests(
        session,
        student_ids=reach,
        statuses=statuses,
        received_before=before if overdue else None,
        after=_after(cursor),
        limit=limit + 1,
    )
    page, more = rows[:limit], len(rows) > limit
    next_cursor = (
        encode_cursor({"d": page[-1].received_on.isoformat(), "i": str(page[-1].id)})
        if more and page
        else None
    )
    cfg = settings()
    return TcRequestList(
        data=_outs(session, ctx, page),
        next_cursor=next_cursor,
        overdue_count=repo.count_overdue(session, student_ids=reach, received_before=before),
        overdue_after_days=cfg.overdue_after_days,
        overdue_verified=cfg.verified,
    )


def get_request(session: Session, ctx: UserContext, request_id: uuid.UUID) -> TcRequestOut:
    """One request with its steps and, for holders of ``finance.read``, the fee note
    (FR-CERT-038; read, issue or approve permission; 404 outside scope)."""
    row = _load(session, ctx, request_id, permissions=_READERS)
    return _outs(session, ctx, [row], with_fee_note=True)[0]


def log_request(session: Session, ctx: UserContext, data: TcRequestIn) -> TcRequestOut:
    """Log a request (FR-CERT-026, FR-CERT-027; permission ``certificate.issue``; 404 outside
    scope). A TC already waiting for approval or issued for the student is linked at once.
    Errors: 422 ``date_in_future``; 409 ``tc_request_open_exists``. Audit
    ``tc_request.logged``."""
    if not certificates._in_reach(
        certificates._reach(session, ctx, [certificates.ISSUE]), data.student_id
    ):
        raise NotFound("Student not found")
    students.get_profile(session, ctx, data.student_id)  # 404 when it does not exist
    today = certificates._today(session)
    if data.received_on > today:
        raise ValidationFailed([_error("received_on", "date_in_future")])
    now = certificates._now(session)
    values: dict[str, object] = {
        "id": new_id(),
        "tenant_id": cert_repo.current_tenant_id(session),
        "student_id": data.student_id,
        "status": "received",
        "received_on": data.received_on,
        "channel": data.channel,
        "leaving_date": data.leaving_date,
        "leaving_reason": data.leaving_reason,
        "received_by": ctx.membership_id,
        "received_at": now,
    }
    live = _live_tc(session, data.student_id)
    if live is not None:
        values.update(certificate_id=live.id, preparing_by=ctx.membership_id, preparing_at=now)
        if live.status == "issued":
            values.update(status="ready", ready_by=ctx.membership_id, ready_at=now)
        else:
            values.update(status="in_preparation")
    with _translated():
        row = repo.insert_request(session, **values)
    certificates._audit(
        session,
        action=LOGGED_ACTION,
        resource_type="tc_request",
        resource_id=row.id,
        summary={
            "student_id": row.student_id,
            "channel": row.channel,
            "received_on": row.received_on.isoformat(),
            "status": row.status,
            "certificate_id": row.certificate_id,
        },
    )
    log.info("tc_request.logged", resource_type="tc_request", resource_id=row.id)
    return _outs(session, ctx, [row])[0]


def _live_tc(session: Session, student_id: uuid.UUID) -> Certificate | None:
    """The student's original TC waiting for approval or issued, if any."""
    issued = cert_repo.transfer_certificates_of(session, [student_id]).get(student_id)
    if issued is not None:
        return issued
    pending = cert_repo.list_certificates(
        session,
        student_ids=None,
        student_id=student_id,
        certificate_type="transfer",
        status="pending",
        limit=5,
    )
    return next((c for c in pending if c.original_certificate_id is None), None)


@contextmanager
def _translated() -> Iterator[None]:
    """Map the request log's own constraint (one open request per student) to a 409."""
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def change_status(
    session: Session,
    ctx: UserContext,
    request_id: uuid.UUID,
    data: TcRequestStatusIn,
    *,
    expected_version: int,
) -> TcRequestOut:
    """Move a request on (FR-CERT-030, FR-CERT-034; permission ``certificate.issue``,
    ``If-Match``): start preparing it, hand the ready TC over, or cancel it with a reason.
    Errors: 404; 412; 422 reason; 409 ``tc_request_wrong_status``,
    ``tc_request_certificate_pending``, ``tc_request_certificate_not_issued``. Audit
    ``tc_request.status_changed``."""
    row = _load(session, ctx, request_id, permissions=[certificates.ISSUE], lock=True)
    if row.version != expected_version:
        raise PreconditionFailed("This request was changed by someone else. Reload it.")
    if row.status not in _MOVES[data.status]:
        raise TcRequestWrongStatus()
    cert = cert_repo.get_certificate(session, row.certificate_id) if row.certificate_id else None
    now = certificates._now(session)
    values: dict[str, object] = {"status": data.status}
    if data.status == "in_preparation":
        values.update(preparing_by=ctx.membership_id, preparing_at=now)
    elif data.status == "issued":
        if cert is None or cert.status != "issued":
            raise TcRequestCertificateNotIssued()
        values.update(issued_by=ctx.membership_id, issued_at=now)
    else:
        if cert is not None and cert.status == "pending":
            raise TcRequestCertificatePending()
        reason = certificates._clean_reason("reason", data.reason, required=True)
        values.update(cancelled_by=ctx.membership_id, cancelled_at=now, cancel_reason=reason)
    previous = row.status
    with _translated():
        moved = repo.update_request(session, row.id, values)
    hooks.record_change(
        session, moved, previous=previous, linked=moved.certificate_id, automatic=False
    )
    log.info("tc_request.status_changed", resource_type="tc_request", resource_id=moved.id)
    return _outs(session, ctx, [moved])[0]


__all__ = [
    "LOGGED_ACTION",
    "TcRequestCertificateNotIssued",
    "TcRequestCertificatePending",
    "TcRequestWrongStatus",
    "change_status",
    "get_request",
    "list_requests",
    "log_request",
    "settings",
]
