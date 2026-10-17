"""Maker-checker fee requests: concessions, adjustments, receipt cancellations and refunds
(US-2103..US-2106; FR-FEE-007, FR-FEE-008, FR-FEE-012, FR-FEE-013, FR-FEE-026; ADR-0010, D17;
internal: other modules use ``app.fees.service``).

Every decision is made by a different person than the requester: the service refuses with 403
``self_approval_forbidden`` and a database ``CHECK`` refuses it again. Approving posts the ledger
entries in the same transaction; a decided request never changes (trigger). Receipts are never
edited: a cancellation is a new record plus reversing entries, a refund a voucher in its own
series.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.documents import service as documents
from app.fees import _common as c
from app.fees import ledger, receipts, templates
from app.fees import repository as repo
from app.fees.models import Adjustment, Concession, Receipt, ReceiptCancellation, Refund
from app.fees.money import ZERO, group_indian, in_words, percent_of, spread, total
from app.fees.schemas import (
    AdjustmentCreate,
    AdjustmentOut,
    ApprovalItem,
    CancellationOut,
    CancellationRequest,
    ConcessionCreate,
    ConcessionOut,
    DecisionIn,
    RefundCreate,
    RefundOut,
    RejectIn,
)
from app.identity import service as identity
from app.notifications import service as notifications
from app.students import service as students

REQUESTED_TEMPLATE: Final = "fee.approval_requested"
DECIDED_TEMPLATE: Final = "fee.request_decided"
APPROVER: Final = {
    "concession": c.CONCESSION_APPROVE,
    "adjustment": c.CONCESSION_APPROVE,
    "cancellation": c.RECEIPT_CANCEL,
    "refund": c.REFUND_APPROVE,
}


# --- notifications (FR-FEE-026; ids only) -------------------------------------------------------


def notify_approvers(
    session: Session, kind: str, request_id: uuid.UUID, account_id: uuid.UUID
) -> None:
    notifications.notify(
        session,
        tenant_id=repo.current_tenant_id(session),
        recipients=notifications.PermissionSelector(APPROVER[kind]),
        template_key=REQUESTED_TEMPLATE,
        params={"request_id": str(request_id), "kind": kind, "account_id": str(account_id)},
        resource_id=request_id,
        dedupe_key=f"{REQUESTED_TEMPLATE}:{request_id}",
    )


def _notify_decided(
    session: Session,
    *,
    kind: str,
    request_id: uuid.UUID,
    account_id: uuid.UUID,
    requester: uuid.UUID,
    decision: str,
) -> None:
    notifications.notify(
        session,
        tenant_id=repo.current_tenant_id(session),
        recipients=[requester],
        template_key=DECIDED_TEMPLATE,
        params={
            "request_id": str(request_id),
            "kind": kind,
            "account_id": str(account_id),
            "decision": decision,
        },
        resource_id=request_id,
        dedupe_key=f"{DECIDED_TEMPLATE}:{request_id}",
    )


# --- shared --------------------------------------------------------------------------------------


def _decide_checks(ctx: UserContext, row: Any, expected_version: int | None) -> None:
    c.check_version(row, expected_version)
    if row.status != "pending":
        raise c.RequestNotPending()
    if row.requested_by == ctx.membership_id:
        raise c.SelfApprovalForbidden()


def _names(session: Session, rows: Sequence[Any]) -> dict[uuid.UUID, str]:
    return identity.member_display_names(
        session, c.unique_ids([r.requested_by for r in rows] + [r.decided_by for r in rows])
    )


def _decision_values(
    ctx: UserContext, session: Session, status: str, note: str | None
) -> dict[str, Any]:
    return {
        "status": status,
        "decided_by": ctx.membership_id,
        "decided_at": c.now(session),
        "decision_note": note,
    }


def _account_for(session: Session, account_id: uuid.UUID) -> Any:
    return ledger.load_account(session, account_id, lock=True)


# --- concessions (FR-FEE-008) -------------------------------------------------------------------


def _concession_out(row: Concession, names: dict[uuid.UUID, str]) -> ConcessionOut:
    return ConcessionOut(
        id=row.id,
        account_id=row.account_id,
        concession_type=row.concession_type,
        basis=row.basis,
        value=row.value,
        targets=list(row.targets),
        reason=row.reason,
        evidence_document_id=row.evidence_document_id,
        status=row.status,
        total_amount=row.total_amount,
        requested_by=row.requested_by,
        requested_by_name=names.get(row.requested_by),
        requested_at=row.requested_at,
        decided_by=row.decided_by,
        decided_by_name=names.get(row.decided_by) if row.decided_by else None,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        version=row.version,
    )


def list_concessions(
    session: Session, ctx: UserContext, account_id: uuid.UUID
) -> list[ConcessionOut]:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ, c.CONCESSION_REQUEST, c.CONCESSION_APPROVE)
    ledger.load_account(session, account_id)
    rows = repo.concessions_of(session, account_id)
    names = _names(session, rows)
    return [_concession_out(r, names) for r in rows]


def request_concession(
    session: Session, ctx: UserContext, account_id: uuid.UUID, data: ConcessionCreate
) -> ConcessionOut:
    """Ask for a concession (``fee.concession.request``). Targets must be charged lines of the
    account. Audit ``fee.concession.requested``; the approvers are notified."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.CONCESSION_REQUEST)
    reason = c.clean_reason("reason", data.reason)
    account = _account_for(session, account_id)
    dues = ledger.dues_for(session, account, c.today(session))
    seen: set[tuple[int, uuid.UUID]] = set()
    for i, t in enumerate(data.targets):
        key = (t.instalment_no, t.head_id)
        found = dues.line(*key)
        if key in seen or found is None or found[0].charged <= 0:
            raise ValidationFailed([c.error(f"targets.{i}", "invalid")])
        seen.add(key)
    with c.db_errors():
        row = repo.insert_concession(
            session,
            id=new_id(),
            tenant_id=account.tenant_id,
            account_id=account.id,
            concession_type=data.concession_type,
            basis=data.basis,
            value=data.value,
            targets=[
                {"instalment_no": t.instalment_no, "head_id": str(t.head_id)} for t in data.targets
            ],
            reason=reason,
            evidence_document_id=data.evidence_document_id,
            requested_by=ctx.membership_id,
        )
    c.record(
        session,
        action="fee.concession.requested",
        resource_type="fee_concession",
        resource_id=row.id,
        summary={
            "account_id": account.id,
            "concession_type": row.concession_type,
            "basis": row.basis,
            "value": c.money_text(row.value),
            "targets": len(data.targets),
        },
    )
    notify_approvers(session, "concession", row.id, account.id)
    return _concession_out(row, _names(session, [row]))


def _load_concession(session: Session, row_id: uuid.UUID) -> Concession:
    row = repo.get_concession(session, row_id, lock=True)
    if row is None:
        raise NotFound("Concession not found")
    return row


def approve_concession(
    session: Session,
    ctx: UserContext,
    row_id: uuid.UUID,
    data: DecisionIn,
    *,
    expected_version: int | None,
) -> ConcessionOut:
    """Approve and post the concession entries in one transaction (``fee.concession.approve``,
    step-up, not the requester). Percent: of each target's charge; amount: spread over the
    targets in order. Never more than what was charged less earlier concessions (409
    ``nothing_to_concede``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.CONCESSION_APPROVE)
    c.require_recent_mfa(ctx)
    row = _load_concession(session, row_id)
    _decide_checks(ctx, row, expected_version)
    note = c.clean_reason("note", data.note, required=False)
    account = _account_for(session, row.account_id)
    today = c.today(session)
    dues = ledger.dues_for(session, account, today)
    targets = [(int(t["instalment_no"]), uuid.UUID(str(t["head_id"]))) for t in row.targets]
    room: list[Decimal] = []
    charged: list[Decimal] = []
    for key in targets:
        found = dues.line(*key)
        bucket = found[0] if found else None
        charged.append(bucket.charged if bucket else ZERO)
        room.append(max((bucket.charged + bucket.concession) if bucket else ZERO, ZERO))
    if row.basis == "percent":
        parts = [min(percent_of(ch, row.value), r) for ch, r in zip(charged, room, strict=True)]
    else:
        try:
            parts = spread(min(row.value, total(room)), room)
        except ValueError:  # pragma: no cover - min() above keeps it within room
            parts = []
    amount = total(parts)
    if amount <= 0:
        raise Conflict(
            "There is nothing left to concede on these lines.", code="nothing_to_concede"
        )
    with c.db_errors():
        row = repo.update_concession(
            session,
            row.id,
            {**_decision_values(ctx, session, "approved", note), "total_amount": amount},
        )
        repo.insert_entries(
            session,
            [
                {
                    "id": new_id(),
                    "tenant_id": account.tenant_id,
                    "account_id": account.id,
                    "entry_kind": "concession",
                    "amount": -part,
                    "instalment_no": inst_no,
                    "head_id": head_id,
                    "entry_date": today,
                    "concession_id": row.id,
                    "posted_by": ctx.membership_id,
                }
                for (inst_no, head_id), part in zip(targets, parts, strict=True)
                if part > 0
            ],
        )
    c.record(
        session,
        action="fee.concession.approved",
        resource_type="fee_concession",
        resource_id=row.id,
        summary={
            "account_id": account.id,
            "total_amount": c.money_text(amount),
            "requested_by": row.requested_by,
        },
    )
    _notify_decided(
        session,
        kind="concession",
        request_id=row.id,
        account_id=account.id,
        requester=row.requested_by,
        decision="approved",
    )
    return _concession_out(row, _names(session, [row]))


def reject_concession(
    session: Session,
    ctx: UserContext,
    row_id: uuid.UUID,
    data: RejectIn,
    *,
    expected_version: int | None,
) -> ConcessionOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.CONCESSION_APPROVE)
    c.require_recent_mfa(ctx)
    row = _load_concession(session, row_id)
    _decide_checks(ctx, row, expected_version)
    reason = c.clean_reason("reason", data.reason)
    with c.db_errors():
        row = repo.update_concession(
            session, row.id, _decision_values(ctx, session, "rejected", reason)
        )
    c.record(
        session,
        action="fee.concession.rejected",
        resource_type="fee_concession",
        resource_id=row.id,
        summary={"account_id": row.account_id, "requested_by": row.requested_by},
    )
    _notify_decided(
        session,
        kind="concession",
        request_id=row.id,
        account_id=row.account_id,
        requester=row.requested_by,
        decision="rejected",
    )
    return _concession_out(row, _names(session, [row]))


def withdraw_concession(session: Session, ctx: UserContext, row_id: uuid.UUID) -> ConcessionOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.CONCESSION_REQUEST)
    row = _load_concession(session, row_id)
    if row.status != "pending":
        raise c.RequestNotPending()
    if row.requested_by != ctx.membership_id:
        raise c.NotRequester()
    with c.db_errors():
        row = repo.update_concession(session, row.id, {"status": "withdrawn"})
    c.record(
        session,
        action="fee.concession.withdrawn",
        resource_type="fee_concession",
        resource_id=row.id,
        summary={"account_id": row.account_id},
    )
    return _concession_out(row, _names(session, [row]))


# --- adjustments (FR-FEE-007) -------------------------------------------------------------------


def _adjustment_out(row: Adjustment, names: dict[uuid.UUID, str]) -> AdjustmentOut:
    return AdjustmentOut(
        id=row.id,
        account_id=row.account_id,
        instalment_no=row.instalment_no,
        head_id=row.head_id,
        amount=row.amount,
        reason_code=row.reason_code,
        reason=row.reason,
        source_plan_id=row.source_plan_id,
        status=row.status,
        requested_by=row.requested_by,
        requested_by_name=names.get(row.requested_by),
        requested_at=row.requested_at,
        decided_by=row.decided_by,
        decided_by_name=names.get(row.decided_by) if row.decided_by else None,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        version=row.version,
    )


def list_adjustments(
    session: Session, ctx: UserContext, account_id: uuid.UUID
) -> list[AdjustmentOut]:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ, c.MANAGE, c.CONCESSION_APPROVE)
    ledger.load_account(session, account_id)
    rows = repo.adjustments_of(session, account_id)
    names = _names(session, rows)
    return [_adjustment_out(r, names) for r in rows]


def request_adjustment(
    session: Session, ctx: UserContext, account_id: uuid.UUID, data: AdjustmentCreate
) -> AdjustmentOut:
    """Ask for a manual adjustment or a late-fee waiver (``fee.manage``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    reason = c.clean_reason("reason", data.reason)
    account = _account_for(session, account_id)
    if data.amount == 0:
        raise ValidationFailed([c.error("amount", "zero")])
    if data.reason_code == "late_fee_waiver":
        if data.head_id is not None or data.amount >= 0:
            raise ValidationFailed([c.error("amount", "waiver_reduces_late_fee")])
        dues = ledger.dues_for(session, account, c.today(session))
        found = dues.line(data.instalment_no, None)
        if found is None or found[0].late_fee < -data.amount:
            raise ValidationFailed([c.error("amount", "above_late_fee")])
    elif data.head_id is None or repo.get_head(session, data.head_id) is None:
        raise ValidationFailed([c.error("head_id", "invalid")])
    if abs(data.amount) > c.settings().limits.max_amount:
        raise ValidationFailed([c.error("amount", "too_large")])
    with c.db_errors():
        row = repo.insert_adjustment(
            session,
            id=new_id(),
            tenant_id=account.tenant_id,
            account_id=account.id,
            instalment_no=data.instalment_no,
            head_id=data.head_id,
            amount=data.amount,
            reason_code=data.reason_code,
            reason=reason,
            requested_by=ctx.membership_id,
        )
    c.record(
        session,
        action="fee.adjustment.requested",
        resource_type="fee_adjustment",
        resource_id=row.id,
        summary={
            "account_id": account.id,
            "reason_code": row.reason_code,
            "amount": c.money_text(row.amount),
        },
    )
    notify_approvers(session, "adjustment", row.id, account.id)
    return _adjustment_out(row, _names(session, [row]))


def _load_adjustment(session: Session, row_id: uuid.UUID) -> Adjustment:
    row = repo.get_adjustment(session, row_id, lock=True)
    if row is None:
        raise NotFound("Adjustment not found")
    return row


def approve_adjustment(
    session: Session,
    ctx: UserContext,
    row_id: uuid.UUID,
    data: DecisionIn,
    *,
    expected_version: int | None,
) -> AdjustmentOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.CONCESSION_APPROVE)
    c.require_recent_mfa(ctx)
    row = _load_adjustment(session, row_id)
    _decide_checks(ctx, row, expected_version)
    note = c.clean_reason("note", data.note, required=False)
    account = _account_for(session, row.account_id)
    today = c.today(session)
    with c.db_errors():
        row = repo.update_adjustment(
            session, row.id, _decision_values(ctx, session, "approved", note)
        )
        repo.insert_entries(
            session,
            [
                {
                    "id": new_id(),
                    "tenant_id": account.tenant_id,
                    "account_id": account.id,
                    "entry_kind": "adjustment",
                    "amount": row.amount,
                    "instalment_no": row.instalment_no,
                    "head_id": row.head_id,
                    "entry_date": today,
                    "adjustment_id": row.id,
                    "posted_by": ctx.membership_id,
                }
            ],
        )
    c.record(
        session,
        action="fee.adjustment.approved",
        resource_type="fee_adjustment",
        resource_id=row.id,
        summary={
            "account_id": account.id,
            "amount": c.money_text(row.amount),
            "reason_code": row.reason_code,
            "requested_by": row.requested_by,
        },
    )
    _notify_decided(
        session,
        kind="adjustment",
        request_id=row.id,
        account_id=account.id,
        requester=row.requested_by,
        decision="approved",
    )
    return _adjustment_out(row, _names(session, [row]))


def reject_adjustment(
    session: Session,
    ctx: UserContext,
    row_id: uuid.UUID,
    data: RejectIn,
    *,
    expected_version: int | None,
) -> AdjustmentOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.CONCESSION_APPROVE)
    c.require_recent_mfa(ctx)
    row = _load_adjustment(session, row_id)
    _decide_checks(ctx, row, expected_version)
    reason = c.clean_reason("reason", data.reason)
    with c.db_errors():
        row = repo.update_adjustment(
            session, row.id, _decision_values(ctx, session, "rejected", reason)
        )
    c.record(
        session,
        action="fee.adjustment.rejected",
        resource_type="fee_adjustment",
        resource_id=row.id,
        summary={"account_id": row.account_id, "requested_by": row.requested_by},
    )
    _notify_decided(
        session,
        kind="adjustment",
        request_id=row.id,
        account_id=row.account_id,
        requester=row.requested_by,
        decision="rejected",
    )
    return _adjustment_out(row, _names(session, [row]))


def withdraw_adjustment(session: Session, ctx: UserContext, row_id: uuid.UUID) -> AdjustmentOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.MANAGE)
    row = _load_adjustment(session, row_id)
    if row.status != "pending":
        raise c.RequestNotPending()
    if row.requested_by != ctx.membership_id:
        raise c.NotRequester()
    with c.db_errors():
        row = repo.update_adjustment(session, row.id, {"status": "withdrawn"})
    c.record(
        session,
        action="fee.adjustment.withdrawn",
        resource_type="fee_adjustment",
        resource_id=row.id,
        summary={"account_id": row.account_id},
    )
    return _adjustment_out(row, _names(session, [row]))


# --- cancellations (FR-FEE-012) -----------------------------------------------------------------


def _cancel_out(session: Session, row: ReceiptCancellation, receipt: Receipt) -> CancellationOut:
    return receipts.cancellation_out(row, receipt.receipt_number, _names(session, [row]))


def request_cancellation(
    session: Session, ctx: UserContext, receipt_id: uuid.UUID, data: CancellationRequest
) -> CancellationOut:
    """Ask for a receipt to be cancelled (``fee.collect`` or ``fee.manage``). A receipt with a
    refund is not cancelled (409 ``receipt_has_refunds``); an online receipt only as a duplicate
    entry (its money goes back by an online refund). Audit ``fee.receipt.cancel_requested``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.MANAGE)
    reason = c.clean_reason("reason", data.reason)
    receipt = receipts.load_receipt(session, receipt_id, lock=True)
    if receipt.status != "issued":
        raise receipts.ReceiptCancelled()
    if receipt.mode == "razorpay" and data.reason_code != "duplicate_entry":
        raise Conflict(
            "An online receipt is cancelled only as a duplicate entry. Refund it instead.",
            code="refund_first",
        )
    if receipts.committed_refunds(repo.refunds_of(session, [receipt.id])) > 0:
        raise Conflict(
            "This receipt has a refund, so it cannot be cancelled.", code="receipt_has_refunds"
        )
    with c.db_errors():
        row = repo.insert_cancellation(
            session,
            id=new_id(),
            tenant_id=receipt.tenant_id,
            receipt_id=receipt.id,
            reason_code=data.reason_code,
            reason=reason,
            requested_by=ctx.membership_id,
        )
    c.record(
        session,
        action="fee.receipt.cancel_requested",
        resource_type="fee_receipt",
        resource_id=receipt.id,
        summary={
            "cancellation_id": row.id,
            "receipt_number": receipt.receipt_number,
            "reason_code": row.reason_code,
        },
    )
    notify_approvers(session, "cancellation", row.id, receipt.account_id)
    return _cancel_out(session, row, receipt)


def _load_cancellation(session: Session, row_id: uuid.UUID) -> ReceiptCancellation:
    row = repo.get_cancellation(session, row_id, lock=True)
    if row is None:
        raise NotFound("Cancellation not found")
    return row


def approve_cancellation(
    session: Session,
    ctx: UserContext,
    row_id: uuid.UUID,
    data: DecisionIn,
    *,
    expected_version: int | None,
) -> CancellationOut:
    """Cancel the receipt (``fee.receipt.cancel``, step-up, not the requester): one transaction
    writes the decision, the ``payment_reversal`` entries and the receipt status; the receipt
    keeps its number and its stored PDF is archived. Dated today (409 ``day_closed``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECEIPT_CANCEL)
    c.require_recent_mfa(ctx)
    row = _load_cancellation(session, row_id)
    _decide_checks(ctx, row, expected_version)
    note = c.clean_reason("note", data.note, required=False)
    receipt = receipts.load_receipt(session, row.receipt_id, lock=True)
    if receipt.status != "issued":
        raise receipts.ReceiptCancelled()
    if receipts.committed_refunds(repo.refunds_of(session, [receipt.id])) > 0:
        raise Conflict(
            "This receipt has a refund, so it cannot be cancelled.", code="receipt_has_refunds"
        )
    account = _account_for(session, receipt.account_id)
    today = c.today(session)
    c.ensure_day_open(session, today)
    allocations = repo.allocations_of(session, [receipt.id])
    with c.db_errors():
        row = repo.update_cancellation(
            session,
            row.id,
            {**_decision_values(ctx, session, "approved", note), "cancelled_on": today},
        )
        repo.update_receipt(session, receipt.id, {"status": "cancelled"})
        repo.insert_entries(
            session,
            [
                {
                    "id": new_id(),
                    "tenant_id": receipt.tenant_id,
                    "account_id": account.id,
                    "entry_kind": "payment_reversal",
                    "amount": a.amount,
                    "instalment_no": a.instalment_no,
                    "head_id": a.head_id,
                    "entry_date": today,
                    "receipt_id": receipt.id,
                    "cancellation_id": row.id,
                    "posted_by": ctx.membership_id,
                }
                for a in allocations
            ],
        )
    if receipt.document_id is not None:
        documents.archive_generated_document(session, receipt.document_id)
    c.record(
        session,
        action="fee.receipt.cancelled",
        resource_type="fee_receipt",
        resource_id=receipt.id,
        summary={
            "cancellation_id": row.id,
            "receipt_number": receipt.receipt_number,
            "amount": c.money_text(receipt.amount),
            "reason_code": row.reason_code,
            "requested_by": row.requested_by,
        },
    )
    _notify_decided(
        session,
        kind="cancellation",
        request_id=row.id,
        account_id=account.id,
        requester=row.requested_by,
        decision="approved",
    )
    return _cancel_out(session, row, receipt)


def reject_cancellation(
    session: Session,
    ctx: UserContext,
    row_id: uuid.UUID,
    data: RejectIn,
    *,
    expected_version: int | None,
) -> CancellationOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECEIPT_CANCEL)
    c.require_recent_mfa(ctx)
    row = _load_cancellation(session, row_id)
    _decide_checks(ctx, row, expected_version)
    reason = c.clean_reason("reason", data.reason)
    receipt = receipts.load_receipt(session, row.receipt_id)
    with c.db_errors():
        row = repo.update_cancellation(
            session, row.id, _decision_values(ctx, session, "rejected", reason)
        )
    c.record(
        session,
        action="fee.receipt.cancel_rejected",
        resource_type="fee_receipt",
        resource_id=receipt.id,
        summary={"cancellation_id": row.id, "requested_by": row.requested_by},
    )
    _notify_decided(
        session,
        kind="cancellation",
        request_id=row.id,
        account_id=receipt.account_id,
        requester=row.requested_by,
        decision="rejected",
    )
    return _cancel_out(session, row, receipt)


def withdraw_cancellation(session: Session, ctx: UserContext, row_id: uuid.UUID) -> CancellationOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.MANAGE)
    row = _load_cancellation(session, row_id)
    if row.status != "pending":
        raise c.RequestNotPending()
    if row.requested_by != ctx.membership_id:
        raise c.NotRequester()
    receipt = receipts.load_receipt(session, row.receipt_id)
    with c.db_errors():
        row = repo.update_cancellation(session, row.id, {"status": "withdrawn"})
    c.record(
        session,
        action="fee.receipt.cancel_withdrawn",
        resource_type="fee_receipt",
        resource_id=receipt.id,
        summary={"cancellation_id": row.id},
    )
    return _cancel_out(session, row, receipt)


# --- refunds (FR-FEE-013) -----------------------------------------------------------------------


def _refund_out(session: Session, row: Refund, receipt: Receipt) -> RefundOut:
    return receipts.refund_out(row, receipt.receipt_number, _names(session, [row]))


def request_refund(
    session: Session, ctx: UserContext, receipt_id: uuid.UUID, data: RefundCreate
) -> RefundOut:
    """Ask for a refund against an issued receipt (``fee.refund.request``): at most what it took
    less refunds already asked for or paid (409 ``refund_exceeds_paid``). Online receipts are
    refunded through the payment provider (a later wave): 409 ``payments_not_connected``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.REFUND_REQUEST)
    reason = c.clean_reason("reason", data.reason)
    receipt = receipts.load_receipt(session, receipt_id, lock=True)
    if receipt.status != "issued":
        raise receipts.ReceiptCancelled()
    if receipt.mode == "razorpay":
        raise Conflict(
            "Online payments are refunded through the school's payment account, which is not "
            "connected yet.",
            code="payments_not_connected",
        )
    details: dict[str, str] = {}
    if data.mode == "cheque":
        if not data.mode_details.cheque_number:
            raise ValidationFailed([c.error("mode_details.cheque_number", "missing")])
        details["cheque_number"] = data.mode_details.cheque_number
    elif data.mode == "bank_transfer" and data.mode_details.reference:
        details["reference"] = data.mode_details.reference.upper()
    taken = receipts.committed_refunds(repo.refunds_of(session, [receipt.id]))
    if data.amount > receipt.amount - taken:
        raise Conflict(
            "The refund is more than this receipt took and was not refunded yet.",
            code="refund_exceeds_paid",
        )
    with c.db_errors():
        row = repo.insert_refund(
            session,
            id=new_id(),
            tenant_id=receipt.tenant_id,
            receipt_id=receipt.id,
            account_id=receipt.account_id,
            amount=data.amount,
            mode=data.mode,
            mode_details=details,
            reason=reason,
            requested_by=ctx.membership_id,
        )
    c.record(
        session,
        action="fee.refund.requested",
        resource_type="fee_refund",
        resource_id=row.id,
        summary={
            "receipt_id": receipt.id,
            "receipt_number": receipt.receipt_number,
            "amount": c.money_text(row.amount),
            "mode": row.mode,
        },
    )
    notify_approvers(session, "refund", row.id, receipt.account_id)
    return _refund_out(session, row, receipt)


def _load_refund(session: Session, row_id: uuid.UUID) -> Refund:
    row = repo.get_refund(session, row_id, lock=True)
    if row is None:
        raise NotFound("Refund not found")
    return row


def _refund_parts(
    session: Session, receipt: Receipt, amount: Decimal
) -> list[tuple[int, uuid.UUID | None, Decimal]]:
    """Spread a refund over the receipt's lines, newest instalment first, within what each line
    took less what was refunded from it before."""
    allocations = repo.allocations_of(session, [receipt.id])
    refunded: dict[tuple[int, uuid.UUID | None], Decimal] = defaultdict(lambda: ZERO)
    for e in repo.entries_of(session, receipt.account_id):
        if e.entry_kind == "refund" and e.receipt_id == receipt.id:
            refunded[(e.instalment_no, e.head_id)] += e.amount
    ordered = sorted(allocations, key=lambda a: (a.instalment_no, a.head_id is None), reverse=True)
    room = [a.amount - refunded[(a.instalment_no, a.head_id)] for a in ordered]
    parts = spread(amount, room)
    return [(a.instalment_no, a.head_id, p) for a, p in zip(ordered, parts, strict=True) if p > 0]


def approve_refund(
    session: Session,
    ctx: UserContext,
    row_id: uuid.UUID,
    data: DecisionIn,
    *,
    expected_version: int | None,
) -> RefundOut:
    """Approve a cash, cheque or bank-transfer refund (``fee.refund.approve``, step-up, not the
    requester): in one transaction the voucher takes the next number of its own series
    (``RFD/2026-27/0001``), its content is frozen and the ``refund`` ledger entries are posted,
    dated today (409 ``day_closed``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.REFUND_APPROVE)
    c.require_recent_mfa(ctx)
    row = _load_refund(session, row_id)
    _decide_checks(ctx, row, expected_version)
    note = c.clean_reason("note", data.note, required=False)
    receipt = receipts.load_receipt(session, row.receipt_id, lock=True)
    if receipt.status != "issued":
        raise receipts.ReceiptCancelled()
    others = [r for r in repo.refunds_of(session, [receipt.id]) if r.id != row.id]
    if row.amount > receipt.amount - receipts.committed_refunds(others):
        raise Conflict(
            "The refund is more than this receipt took and was not refunded yet.",
            code="refund_exceeds_paid",
        )
    account = _account_for(session, receipt.account_id)
    today = c.today(session)
    c.ensure_day_open(session, today)
    parts = _refund_parts(session, receipt, row.amount)
    financial_year = c.fy(today)
    numbering = c.settings().numbering.refund
    number = repo.allocate_number(session, numbering.series, financial_year)
    voucher_number = numbering.render(financial_year, number)
    content: dict[str, Any] = {
        "voucher_number": voucher_number,
        "voucher_date": today.isoformat(),
        "financial_year": financial_year,
        "receipt_number": receipt.receipt_number,
        "student": dict(receipt.content.get("student") or {}),
        "amount": f"{row.amount:.2f}",
        "amount_text": group_indian(row.amount),
        "amount_in_words": in_words(row.amount),
        "mode": row.mode,
        "mode_details": dict(row.mode_details or {}),
    }
    with c.db_errors():
        row = repo.update_refund(
            session,
            row.id,
            {
                **_decision_values(ctx, session, "processed", note),
                "financial_year": financial_year,
                "voucher_no": number,
                "voucher_number": voucher_number,
                "voucher_date": today,
                "content": content,
                "content_sha256": receipts.content_digest(content),
                "processed_at": c.now(session),
            },
        )
        repo.insert_entries(
            session,
            [
                {
                    "id": new_id(),
                    "tenant_id": receipt.tenant_id,
                    "account_id": account.id,
                    "entry_kind": "refund",
                    "amount": part,
                    "instalment_no": inst_no,
                    "head_id": head_id,
                    "entry_date": today,
                    "receipt_id": receipt.id,
                    "refund_id": row.id,
                    "posted_by": ctx.membership_id,
                }
                for inst_no, head_id, part in parts
            ],
        )
    c.record(
        session,
        action="fee.refund.approved",
        resource_type="fee_refund",
        resource_id=row.id,
        summary={
            "voucher_number": voucher_number,
            "receipt_number": receipt.receipt_number,
            "amount": c.money_text(row.amount),
            "mode": row.mode,
            "requested_by": row.requested_by,
        },
    )
    _notify_decided(
        session,
        kind="refund",
        request_id=row.id,
        account_id=account.id,
        requester=row.requested_by,
        decision="approved",
    )
    return _refund_out(session, row, receipt)


def reject_refund(
    session: Session,
    ctx: UserContext,
    row_id: uuid.UUID,
    data: RejectIn,
    *,
    expected_version: int | None,
) -> RefundOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.REFUND_APPROVE)
    c.require_recent_mfa(ctx)
    row = _load_refund(session, row_id)
    _decide_checks(ctx, row, expected_version)
    reason = c.clean_reason("reason", data.reason)
    receipt = receipts.load_receipt(session, row.receipt_id)
    with c.db_errors():
        row = repo.update_refund(
            session, row.id, _decision_values(ctx, session, "rejected", reason)
        )
    c.record(
        session,
        action="fee.refund.rejected",
        resource_type="fee_refund",
        resource_id=row.id,
        summary={"receipt_id": receipt.id, "requested_by": row.requested_by},
    )
    _notify_decided(
        session,
        kind="refund",
        request_id=row.id,
        account_id=receipt.account_id,
        requester=row.requested_by,
        decision="rejected",
    )
    return _refund_out(session, row, receipt)


def withdraw_refund(session: Session, ctx: UserContext, row_id: uuid.UUID) -> RefundOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.REFUND_REQUEST)
    row = _load_refund(session, row_id)
    if row.status != "pending":
        raise c.RequestNotPending()
    if row.requested_by != ctx.membership_id:
        raise c.NotRequester()
    receipt = receipts.load_receipt(session, row.receipt_id)
    with c.db_errors():
        row = repo.update_refund(session, row.id, {"status": "withdrawn"})
    c.record(
        session,
        action="fee.refund.withdrawn",
        resource_type="fee_refund",
        resource_id=row.id,
        summary={"receipt_id": receipt.id},
    )
    return _refund_out(session, row, receipt)


def voucher_page(session: Session, ctx: UserContext, refund_id: uuid.UUID) -> str:
    """The print view (HTML, A5) of a processed refund voucher (FR-FEE-013; ``fee.collect`` or
    ``finance.read``). 409 ``voucher_not_ready`` before the refund is paid out. Audit
    ``fee.refund.print_viewed``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    row = repo.get_refund(session, refund_id)
    if row is None:
        raise NotFound("Refund not found")
    if row.status != "processed" or row.content is None:
        raise Conflict(
            "This refund has no voucher yet: it is written when the refund is approved.",
            code="voucher_not_ready",
        )
    receipt = receipts.load_receipt(session, row.receipt_id)
    content = {**row.content, "school": dict(receipt.content.get("school") or {})}
    page = templates.render_voucher(content)
    c.record(
        session,
        action="fee.refund.print_viewed",
        resource_type="fee_refund",
        resource_id=row.id,
        summary={"voucher_number": row.voucher_number},
    )
    return page


# --- the approvals inbox -------------------------------------------------------------------------


def approvals(session: Session, ctx: UserContext) -> list[ApprovalItem]:
    """Every pending fee request the caller can see, oldest first, marked ``mine`` (never
    decidable by its maker) and ``can_decide`` (holds the approving permission and is not the
    maker). Any approving or requesting fee permission, or ``finance.read``."""
    c.require_on(session, ctx)
    c.require_school_wide(
        ctx,
        c.READ,
        c.CONCESSION_APPROVE,
        c.RECEIPT_CANCEL,
        c.REFUND_APPROVE,
        c.CONCESSION_REQUEST,
        c.REFUND_REQUEST,
        c.MANAGE,
        c.COLLECT,
    )
    pending = repo.pending_requests(session, c.settings().limits.list_max)
    cancellation_receipts = repo.receipts_by_id(
        session, {r.receipt_id for r in pending["cancellation"]}
    )
    refund_receipts = repo.receipts_by_id(session, {r.receipt_id for r in pending["refund"]})
    account_ids = {r.account_id for k in ("concession", "adjustment", "refund") for r in pending[k]}
    account_ids |= {r.account_id for r in cancellation_receipts.values()}
    accounts = repo.accounts_by_id(session, account_ids)
    people = students.summaries(session, ctx, {a.student_id for a in accounts.values()})
    names = identity.member_display_names(
        session, {r.requested_by for rows in pending.values() for r in rows}
    )
    items: list[ApprovalItem] = []

    def add(
        *,
        kind: str,
        row: Any,
        account_id: uuid.UUID,
        amount: Decimal | None,
        code: str,
        receipt: Receipt | None,
    ) -> None:
        account = accounts.get(account_id)
        if account is None:
            return
        student = people.get(account.student_id)
        mine = row.requested_by == ctx.membership_id
        items.append(
            ApprovalItem(
                kind=kind,
                id=row.id,
                account_id=account_id,
                student_id=account.student_id,
                student_name=student.display_name if student else None,
                class_section=student.class_section if student else None,
                amount=amount,
                summary_code=code,
                reason=row.reason,
                requested_by=row.requested_by,
                requested_by_name=names.get(row.requested_by),
                requested_at=row.requested_at,
                receipt_id=receipt.id if receipt else None,
                receipt_number=receipt.receipt_number if receipt else None,
                mine=mine,
                can_decide=not mine and c.holds(ctx, APPROVER[kind]),
                version=row.version,
            )
        )

    for row in pending["concession"]:
        add(
            kind="concession",
            row=row,
            account_id=row.account_id,
            amount=row.value if row.basis == "amount" else None,
            code=f"{row.concession_type}:{row.basis}",
            receipt=None,
        )
    for row in pending["adjustment"]:
        add(
            kind="adjustment",
            row=row,
            account_id=row.account_id,
            amount=row.amount,
            code=row.reason_code,
            receipt=None,
        )
    for row in pending["cancellation"]:
        receipt = cancellation_receipts.get(row.receipt_id)
        if receipt is not None:
            add(
                kind="cancellation",
                row=row,
                account_id=receipt.account_id,
                amount=receipt.amount,
                code=row.reason_code,
                receipt=receipt,
            )
    for row in pending["refund"]:
        add(
            kind="refund",
            row=row,
            account_id=row.account_id,
            amount=row.amount,
            code=row.mode,
            receipt=refund_receipts.get(row.receipt_id),
        )
    items.sort(key=lambda i: (i.requested_at, str(i.id)))
    return items


__all__ = [
    "approvals",
    "approve_adjustment",
    "approve_cancellation",
    "approve_concession",
    "approve_refund",
    "list_adjustments",
    "list_concessions",
    "notify_approvers",
    "reject_adjustment",
    "reject_cancellation",
    "reject_concession",
    "reject_refund",
    "request_adjustment",
    "request_cancellation",
    "request_concession",
    "request_refund",
    "withdraw_adjustment",
    "withdraw_cancellation",
    "withdraw_concession",
    "withdraw_refund",
]
