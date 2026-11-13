"""WhatsApp sending routes (docs/09 §4 "Messaging"; R2-3G; US-2204..US-2208; FR-MSG-012,
FR-MSG-017; FR-PUB-007). Included in ``app.messaging.api.router`` (prefix ``/api/v1``).

Sending declares ``message.send`` school-wide (principal and office admin; manual fee reminders
also need ``finance.read``, checked by ``fees.service``); a receipt's "Send on WhatsApp" declares
``fee.collect`` or ``message.send`` (R2-3J); the reports declare
``message.read`` (a class teacher's scoped grant counts the students of their sections only). On a
dedicated host or with messaging off every route answers ``404 module_unavailable``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import IdempotencyDep
from app.messaging.sends import closures, reach, service
from app.messaging.sends.schemas import (
    AutomaticOut,
    BroadcastConfirmIn,
    BroadcastFollowUpOut,
    BroadcastOut,
    BroadcastPage,
    BroadcastPreviewIn,
    BroadcastPreviewOut,
    BroadcastReportOut,
    ClosureConfirmIn,
    ClosureIn,
    ClosureOut,
    ClosurePage,
    ClosurePreviewOut,
    ClosureReopeningConfirmIn,
    ClosureReopeningIn,
    ClosureReopeningOut,
    ClosureReopeningPreviewOut,
    CoverageOut,
    FeeReminderPreviewOut,
    FeeRemindersConfirmIn,
    FeeReminderSelectionIn,
    FeeRemindersOut,
    NotReachedOut,
    OptinCategoryName,
    ReceiptWhatsAppOut,
    RevokeLinkOut,
)

router = APIRouter()

Sender = Annotated[UserContext, Depends(require(service.SEND, scope="school"))]
Reader = Annotated[UserContext, Depends(require(service.READ))]
SchoolReader = Annotated[UserContext, Depends(require(service.READ, scope="school"))]
# R2-3J: the counter (fee.collect) or a sender; the service requires either school-wide.
ReceiptSender = Annotated[UserContext, Depends(require_any(service.FEE_COLLECT, service.SEND))]

_UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    404: {"description": "``module_unavailable`` on a dedicated host or with messaging off."}
}


@router.post(
    "/notices/{notice_id}/broadcasts/preview",
    response_model=BroadcastPreviewOut,
    responses=_UNAVAILABLE,
)
def preview_broadcast(
    ctx: Sender, db: TenantDB, notice_id: uuid.UUID, body: BroadcastPreviewIn, response: Response
) -> BroadcastPreviewOut:
    """Before sending an approved notice on WhatsApp: recipients, guardians left out by reason,
    languages and the estimated Meta cost incl. GST, paid by the school to Meta (US-2204 AC1;
    ``message.send``). Returns a preview token valid 30 minutes; nothing is sent. Errors: 404;
    409 ``notice_not_approved``; 422 ``section_ids`` (``not_found``, ``not_current_year``,
    ``too_many``)."""
    response.headers["Cache-Control"] = "no-store"
    return service.preview_broadcast(db, ctx, notice_id, body)


@router.post(
    "/notices/{notice_id}/broadcasts",
    response_model=BroadcastOut,
    status_code=202,
    responses=_UNAVAILABLE,
)
def confirm_broadcast(
    *,
    ctx: Sender,
    db: TenantDB,
    notice_id: uuid.UUID,
    body: BroadcastConfirmIn,
    idem: IdempotencyDep,
) -> Response:
    """Send the previewed notice (US-2204 AC2, AC3; ``message.send``; ``Idempotency-Key``): one
    message per opted-in guardian, sent outside quiet hours (21:00-07:00 IST). Errors: 409
    ``preview_invalid``, ``preview_expired``, ``preview_stale`` (preview again),
    ``notice_not_approved``, ``messaging_not_connected``, ``messaging_paused``,
    ``template_not_approved``, ``template_paused``, ``messaging_currency_not_inr``,
    ``notices_off``; 422 ``resend_reason`` (``required`` for "Send again"). Audited."""
    return idem.run(
        db,
        body,
        lambda: service.confirm_broadcast(db, ctx, notice_id, body),
        status_code=202,
        headers=lambda out: {"Location": f"/api/v1/messaging/broadcasts/{out.id}"},
    )


@router.get("/notices/{notice_id}/broadcasts", response_model=BroadcastPage, responses=_UNAVAILABLE)
def notice_broadcasts(
    ctx: Reader,
    db: TenantDB,
    notice_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> BroadcastPage:
    """The notice's broadcasts with live counts (``message.read``)."""
    return service.list_broadcasts(db, ctx, notice_id=notice_id, limit=limit, cursor=cursor)


@router.get("/messaging/broadcasts", response_model=BroadcastPage, responses=_UNAVAILABLE)
def list_broadcasts(
    ctx: Reader,
    db: TenantDB,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> BroadcastPage:
    """Recent broadcasts, newest first, with live delivery counts (FR-MSG-017; ``message.read``)."""
    return service.list_broadcasts(db, ctx, notice_id=None, limit=limit, cursor=cursor)


@router.get(
    "/messaging/broadcasts/{broadcast_id}",
    response_model=BroadcastReportOut,
    responses=_UNAVAILABLE,
)
def broadcast_report(ctx: Reader, db: TenantDB, broadcast_id: uuid.UUID) -> BroadcastReportOut:
    """A broadcast's delivery report: counts by status and by class, failure and skip reasons,
    live (FR-MSG-017, US-2204 AC2; ``message.read``; class teachers: their sections). Counts
    only."""
    return service.broadcast_report(db, ctx, broadcast_id)


@router.get("/messaging/automatic", response_model=AutomaticOut, responses=_UNAVAILABLE)
def automatic(ctx: Reader, db: TenantDB) -> AutomaticOut:
    """Absence alerts, fee reminders and receipt messages: switches, times, no-message dates,
    messages waiting and the last two weeks of runs (US-2205, US-2207; ``message.read``)."""
    return service.automatic(db, ctx)


@router.post(
    "/messages/{message_id}/revoke-link", response_model=RevokeLinkOut, responses=_UNAVAILABLE
)
def revoke_link(ctx: Sender, db: TenantDB, message_id: uuid.UUID) -> RevokeLinkOut:
    """Stop the parent link a message carried from working, at once (ADR-0046 decision 5,
    FR-PUB-007; ``message.send``). ``revoked`` false when it had already stopped. Audited."""
    return service.revoke_link(db, ctx, message_id)


# --- R2-3J: manual fee reminders (FR-MSG-025) and receipts on WhatsApp (FR-MSG-026) ------------


@router.post(
    "/fees/reminders/preview",
    response_model=FeeReminderPreviewOut,
    responses=_UNAVAILABLE,
)
def preview_fee_reminders(
    ctx: Sender, db: TenantDB, body: FeeReminderSelectionIn, response: Response
) -> FeeReminderPreviewOut:
    """Before sending fee reminders from the dues list: accounts chosen, messages to queue,
    accounts and guardians left out by reason, and the estimated Meta cost incl. GST
    (FR-MSG-025; ``message.send`` and ``finance.read``, school-wide). Choose rows
    (``account_ids``) or ``all_matching`` the dues list's filter; ``kind: payment_link`` is
    "Send payment link" (FR-MSG-027: the same message, whose dues page offers "Pay online";
    ``blocked_reason`` ``payments_not_connected`` without online payments). Nothing is sent.
    Errors: 403
    without ``finance.read``; 404 an account that is not one of this year's (or fees off); 422
    the selection, ``account_ids`` ``too_many``."""
    response.headers["Cache-Control"] = "no-store"
    return service.preview_fee_reminders(db, ctx, body)


@router.post(
    "/fees/reminders",
    response_model=FeeRemindersOut,
    status_code=202,
    responses=_UNAVAILABLE,
)
def send_fee_reminders(
    *, ctx: Sender, db: TenantDB, body: FeeRemindersConfirmIn, idem: IdempotencyDep
) -> Response:
    """Send the previewed fee reminders (FR-MSG-025; ``message.send`` and ``finance.read``;
    ``Idempotency-Key``): one per account and opted-in guardian, at most once a day, sent
    outside quiet hours within the daily cap; a payment link (``kind: payment_link``,
    FR-MSG-027) once a day apart from reminders. Errors: 409 ``preview_stale`` (preview again),
    ``payments_not_connected`` (payment links only),
    ``messaging_not_connected``, ``messaging_paused``, ``template_not_approved``,
    ``template_paused``, ``messaging_currency_not_inr``. Audited."""
    return idem.run(db, body, lambda: service.confirm_fee_reminders(db, ctx, body), status_code=202)


@router.get(
    "/fees/receipts/{receipt_id}/whatsapp",
    response_model=ReceiptWhatsAppOut,
    responses=_UNAVAILABLE,
)
def receipt_whatsapp(ctx: ReceiptSender, db: TenantDB, receipt_id: uuid.UUID) -> ReceiptWhatsAppOut:
    """Whether the receipt can be sent on WhatsApp now and the status of its messages
    (FR-MSG-026; ``fee.collect`` or ``message.send``, school-wide). 404 unknown receipt."""
    return service.receipt_whatsapp(db, ctx, receipt_id)


@router.post(
    "/fees/receipts/{receipt_id}/whatsapp",
    response_model=ReceiptWhatsAppOut,
    status_code=202,
    responses=_UNAVAILABLE,
)
def send_receipt_whatsapp(
    *, ctx: ReceiptSender, db: TenantDB, receipt_id: uuid.UUID, idem: IdempotencyDep
) -> Response:
    """Send the receipt on WhatsApp to the guardians opted in for fee messages who have not got
    it yet (FR-MSG-026, US-2206; ``fee.collect`` or ``message.send``; ``Idempotency-Key``);
    never twice to a guardian. Errors: 404; 409 ``receipt_cancelled``, ``no_opted_in_guardian``,
    ``already_queued``, ``messaging_not_connected``, ``messaging_paused``,
    ``template_not_approved``, ``template_paused``, ``messaging_currency_not_inr``. Audited."""
    return idem.run(
        db, None, lambda: service.send_receipt_whatsapp(db, ctx, receipt_id), status_code=202
    )


# --- FP-01: opt-in coverage and reach follow-up (FR-MSG-039..046) -------------------------------


@router.get("/messaging/coverage", response_model=CoverageOut, responses=_UNAVAILABLE)
def coverage(
    ctx: Reader,
    db: TenantDB,
    category: Annotated[OptinCategoryName, Query()] = "notices",
) -> CoverageOut:
    """Opt-in coverage per section of the current year for one category: students, students
    with at least one guardian WhatsApp reaches, guardians opted in and without a mobile number
    (FR-MSG-039, FR-MSG-040; ``message.read``; class teachers: their sections). Counts only."""
    return reach.coverage(db, ctx, category)


@router.get(
    "/sections/{section_id}/optins/not-reached",
    response_model=NotReachedOut,
    responses=_UNAVAILABLE,
)
def not_reached(
    ctx: Reader,
    db: TenantDB,
    section_id: uuid.UUID,
    category: Annotated[OptinCategoryName, Query()] = "notices",
) -> NotReachedOut:
    """The section's students whom no WhatsApp message of the category reaches, with each
    guardian's relation and reason code (FR-MSG-041, FR-MSG-042; ``message.read``, scope: 404
    outside it). Names within the reader's scope; no phone numbers. Print join slips for them
    with ``POST /messaging/join-slips`` (FR-MSG-043)."""
    return reach.not_reached(db, ctx, section_id, category)


@router.get(
    "/messaging/broadcasts/{broadcast_id}/follow-up",
    response_model=BroadcastFollowUpOut,
    responses=_UNAVAILABLE,
)
def broadcast_follow_up(ctx: Reader, db: TenantDB, broadcast_id: uuid.UUID) -> BroadcastFollowUpOut:
    """Students of the broadcast's sections the notice did not reach: not sent, failed, or not
    opened 48 hours after sending (read receipt or parent page view) (FR-MSG-044, FR-MSG-045;
    ``message.read``; class teachers: their sections). A list to act on; nothing is resent."""
    return reach.broadcast_follow_up(db, ctx, broadcast_id)


# --- FP-02: urgent closure message (FR-MSG-047..055; ADR-0043 amendment, D85) ------------------

# Step-up MFA for the preview too (a POST under a step-up permission; SEC-005): the principal
# signs in again once and has five minutes to preview and send.
ClosureSender = Annotated[
    UserContext, Depends(require(closures.SEND, scope="school", step_up=True))
]


@router.post(
    "/messaging/closures/preview", response_model=ClosurePreviewOut, responses=_UNAVAILABLE
)
def preview_closure(
    ctx: ClosureSender, db: TenantDB, body: ClosureIn, response: Response
) -> ClosurePreviewOut:
    """Before sending an urgent closure message: recipients, guardians left out by reason, the
    English text, when sending starts (06:00-21:30 IST window, also during quiet hours) and the
    estimated Meta cost (FR-MSG-048; ``message.closure.send``: the principal). Nothing is sent.
    Errors: 422 ``closed_from`` (``in_past``, ``too_far_ahead``), ``closed_to``
    (``before_closed_from``, ``too_long``), ``section_ids``."""
    response.headers["Cache-Control"] = "no-store"
    return closures.preview_closure(db, ctx, body)


@router.post(
    "/messaging/closures", response_model=ClosureOut, status_code=202, responses=_UNAVAILABLE
)
def send_closure(
    *, ctx: ClosureSender, db: TenantDB, body: ClosureConfirmIn, idem: IdempotencyDep
) -> Response:
    """Send the previewed closure message (FR-MSG-049..053; ``message.closure.send`` with
    step-up MFA, 428 without; ``Idempotency-Key``): the ``school_closure`` utility template to
    every guardian opted in for notices, inside the closure window, within the daily cap.
    Errors: 409 ``preview_stale``, ``messaging_not_connected``, ``messaging_paused``,
    ``template_not_approved``, ``template_paused``, ``messaging_currency_not_inr``,
    ``notices_off``; 422 as the preview. Audited ``messaging.closure.sent``."""
    return idem.run(db, body, lambda: closures.send_closure(db, ctx, body), status_code=202)


@router.get("/messaging/closures", response_model=ClosurePage, responses=_UNAVAILABLE)
def list_closures(
    ctx: SchoolReader,
    db: TenantDB,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ClosurePage:
    """Closure messages sent recently with live delivery counts, their "school reopens"
    messages, and how many were sent in the window shown (FR-MSG-054, FR-MSG-064;
    ``message.read`` school-wide). Counts and the principal's own order text only."""
    return closures.list_closures(db, ctx, limit=limit)


# --- FP-02 follow-up: "the school reopens" (FR-MSG-059..065; ADR-0043 amendment, D86) ----------


@router.post(
    "/messaging/closures/{closure_id}/reopening/preview",
    response_model=ClosureReopeningPreviewOut,
    responses=_UNAVAILABLE,
)
def preview_reopening(
    closure_id: uuid.UUID,
    ctx: ClosureSender,
    db: TenantDB,
    body: ClosureReopeningIn,
    response: Response,
) -> ClosureReopeningPreviewOut:
    """Before sending "the school reopens on <date>" for a closure: recipients (the closure's
    audience as it is now), guardians left out by reason, the English text, when sending starts
    and the estimated Meta cost (FR-MSG-060; ``message.closure.send``: the principal). Nothing
    is sent. Errors: 404 unknown closure; 409 ``reopening_already_sent``; 422 ``reopens_on``
    (``in_past``, ``too_far_ahead``, ``not_after_closure_start``, ``too_long_after_closure``)."""
    response.headers["Cache-Control"] = "no-store"
    return closures.preview_reopening(db, ctx, closure_id, body)


@router.post(
    "/messaging/closures/{closure_id}/reopening",
    response_model=ClosureReopeningOut,
    status_code=202,
    responses=_UNAVAILABLE,
)
def send_reopening(
    *,
    closure_id: uuid.UUID,
    ctx: ClosureSender,
    db: TenantDB,
    body: ClosureReopeningConfirmIn,
    idem: IdempotencyDep,
) -> Response:
    """Send the previewed reopening message (FR-MSG-061..063; ``message.closure.send`` with
    step-up MFA, 428 without; ``Idempotency-Key``): the ``school_reopening`` utility template to
    every guardian opted in for notices, inside the closure window, within the daily cap; one per
    closure. Errors: 404; 409 ``reopening_already_sent``, ``preview_stale`` and the closure's
    blocking reasons; 422 as the preview. Audited ``messaging.closure.reopening_sent``."""
    return idem.run(
        db, body, lambda: closures.send_reopening(db, ctx, closure_id, body), status_code=202
    )


__all__ = ["router"]
