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
from app.messaging.sends import service
from app.messaging.sends.schemas import (
    AutomaticOut,
    BroadcastConfirmIn,
    BroadcastOut,
    BroadcastPage,
    BroadcastPreviewIn,
    BroadcastPreviewOut,
    BroadcastReportOut,
    FeeReminderPreviewOut,
    FeeRemindersConfirmIn,
    FeeReminderSelectionIn,
    FeeRemindersOut,
    ReceiptWhatsAppOut,
    RevokeLinkOut,
)

router = APIRouter()

Sender = Annotated[UserContext, Depends(require(service.SEND, scope="school"))]
Reader = Annotated[UserContext, Depends(require(service.READ))]
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


__all__ = ["router"]
