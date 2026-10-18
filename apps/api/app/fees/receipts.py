"""Receipts at the counter (US-2102; FR-FEE-010, FR-FEE-011, FR-FEE-014, FR-FEE-022,
FR-FEE-023; internal: other modules use ``app.fees.service``).

Issuing is one transaction: lock the account, check the day is open (shared day lock), check the
UTR under its own lock, check the amount against what is due now (no advances) and every line
against its due (collectable heads only), freeze the printed content with its SHA-256, take the
next number from the locked counter row LAST (so the lock is held briefly and a failed save
leaves no gap), write the receipt, its allocations and the payment ledger entries, audit, and
queue the A5/A4 PDF on queue ``pdf``. A deferred trigger checks at commit that allocations and
payments add up to the amount.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.pdf import PdfRenderer, get_renderer
from app.documents import service as documents
from app.fees import _common as c
from app.fees import ledger, links, setup, templates
from app.fees import repository as repo
from app.fees import upi_repository as upi_repo
from app.fees.models import Receipt, ReceiptCancellation, Refund, StudentFeeAccount
from app.fees.money import group_indian, in_words, spread, total
from app.fees.schemas import (
    AllocationIn,
    CancellationOut,
    ReceiptCreate,
    ReceiptDownloadOut,
    ReceiptLineOut,
    ReceiptListItem,
    ReceiptOut,
    RefundOut,
)
from app.identity import service as identity
from app.ops import service as ops
from app.students import service as students
from app.students.schemas import StudentSummary
from app.tenancy import service as tenancy

log = get_logger(__name__)

RENDER_EVENT: Final = "fee.receipt.issued"
RENDER_TASK: Final = "fees.render_receipt"
DOCUMENT_PURPOSE: Final = "fee_receipt"
_FILENAME_RE: Final = re.compile(r"[^A-Za-z0-9._-]+")
LIVE_REFUNDS: Final = frozenset({"pending", "approved", "processing", "processed"})

ops.register_outbox_route(RENDER_EVENT, RENDER_TASK)


class AmountExceedsDue(Conflict):
    code = "amount_exceeds_due"

    def __init__(self) -> None:
        super().__init__(
            "The amount is more than what is due now. Advances are not taken: lower the amount "
            "to the total due or less."
        )


class HeadNotCollectable(Conflict):
    code = "head_not_collectable"

    def __init__(self) -> None:
        super().__init__(
            "A taxable fee head cannot be collected with a SchoolOS receipt. Invoice it in "
            "Tally instead."
        )


class UtrInUse(Conflict):
    code = "utr_in_use"

    def __init__(self, number: str) -> None:
        super().__init__(f"This UTR is already on receipt {number}. Open that receipt.")


class ReceiptCancelled(Conflict):
    code = "receipt_cancelled"

    def __init__(self) -> None:
        super().__init__("This receipt was cancelled. Open it to print it marked CANCELLED.")


# --- content -------------------------------------------------------------------------------------


def content_digest(content: dict[str, Any]) -> bytes:
    """SHA-256 of the canonical JSON of the frozen content (FR-FEE-011)."""
    data = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).digest()


def _school(session: Session) -> dict[str, str]:
    tenant = tenancy.get_tenant(session)
    head = tenant.settings.certificate_letterhead
    return {"name": tenant.name, "address": head.address_en, "affiliation": head.affiliation}


def _mode_details(data: ReceiptCreate) -> dict[str, str]:
    """Only the fields of the chosen mode, checked (FR-FEE-010)."""
    cfg = c.settings().modes
    d = data.mode_details
    if data.mode == "cheque":
        if d.cheque_number is None or not re.match(cfg.cheque_number_pattern, d.cheque_number):
            raise ValidationFailed([c.error("mode_details.cheque_number", "invalid_cheque_number")])
        if not d.bank_name:
            raise ValidationFailed([c.error("mode_details.bank_name", "missing")])
        if len(d.bank_name) > cfg.bank_name_max_length:
            raise ValidationFailed([c.error("mode_details.bank_name", "too_long")])
        c.refuse_aadhaar("mode_details.bank_name", d.bank_name)
        if d.cheque_date is None:
            raise ValidationFailed([c.error("mode_details.cheque_date", "missing")])
        return {
            "cheque_number": d.cheque_number,
            "bank_name": d.bank_name,
            "cheque_date": d.cheque_date.isoformat(),
        }
    if data.mode == "bank_transfer":
        if d.reference is None or not re.match(cfg.bank_reference_pattern, d.reference):
            raise ValidationFailed([c.error("mode_details.reference", "invalid_reference")])
        return {"reference": d.reference.upper()}
    return {}


def _utr(data: ReceiptCreate) -> str | None:
    if data.mode != "upi_qr":
        if data.utr:
            raise ValidationFailed([c.error("utr", "not_for_mode")])
        return None
    if data.utr is None or not re.match(c.settings().modes.utr_pattern, data.utr):
        raise ValidationFailed([c.error("utr", "invalid_utr")])
    return data.utr


def _allocate(
    dues: ledger.Dues, amount: Decimal, wanted: Sequence[AllocationIn] | None
) -> list[tuple[int, uuid.UUID | None, Decimal]]:
    """Oldest due first by default; a given allocation must add up and stay within each line
    (FR-FEE-010). Taxable heads are never collected (FR-FEE-023)."""
    if amount > dues.collectable:
        raise AmountExceedsDue()
    if wanted is None:
        lines = [(b, o) for b, o in dues.lines if o.collectable and o.due > 0]
        parts = spread(amount, [o.due for _, o in lines])
        return [
            (b.instalment_no, b.head_id, p) for (b, _), p in zip(lines, parts, strict=True) if p > 0
        ]
    seen: set[tuple[int, uuid.UUID | None]] = set()
    out: list[tuple[int, uuid.UUID | None, Decimal]] = []
    for i, part in enumerate(wanted):
        key = (part.instalment_no, part.head_id)
        if key in seen:
            raise ValidationFailed([c.error(f"allocations.{i}", "duplicate")])
        seen.add(key)
        found = dues.line(part.instalment_no, part.head_id)
        if found is None:
            raise ValidationFailed([c.error(f"allocations.{i}", "invalid")])
        _, line = found
        if not line.collectable:
            raise HeadNotCollectable()
        if part.amount > line.due:
            raise ValidationFailed([c.error(f"allocations.{i}.amount", "above_due")])
        out.append((part.instalment_no, part.head_id, part.amount))
    if total(p for _, _, p in out) != amount:
        raise ValidationFailed(
            [c.error("allocations", "allocations_do_not_add_up")],
            detail="The allocation must add up to the receipt amount.",
        )
    return out


def _amount_text(value: Decimal) -> str:
    return group_indian(value)


def issue_receipt(session: Session, ctx: UserContext, data: ReceiptCreate) -> ReceiptOut:
    """Issue a counter receipt (``fee.collect``, ``Idempotency-Key``). Errors: 409
    ``amount_exceeds_due``, ``head_not_collectable``, ``utr_in_use``, ``day_closed``,
    ``account_closed``; 422 for mode details, UTR shape and allocations. Audit
    ``fee.receipt.issued`` (number, mode, amount). Open payment links on the instalments it
    pays are closed in the same transaction (FR-PAY-006)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT)
    account = ledger.load_account(session, data.account_id, lock=True)
    if account.status != "open":
        raise Conflict("This fee account is closed.", code="account_closed")
    if data.amount > c.settings().limits.max_amount:
        raise ValidationFailed([c.error("amount", "too_large")])
    details = _mode_details(data)
    utr = _utr(data)
    today = c.today(session)
    c.ensure_day_open(session, today)
    if utr is not None:
        repo.lock_utr(session, utr)
        holder = repo.issued_receipt_with_utr(session, utr)
        if holder is not None:
            raise UtrInUse(holder.receipt_number)
    dues = ledger.dues_for(session, account, today)
    parts = _allocate(dues, data.amount, data.allocations)
    student = students.summaries(session, ctx, [account.student_id]).get(account.student_id)
    if student is None:
        raise NotFound("Fee account not found")
    collector = identity.member_display_names(session, [ctx.membership_id]).get(ctx.membership_id)
    row = write_receipt(
        session,
        account=account,
        amount=data.amount,
        mode=data.mode,
        details=details,
        utr=utr,
        parts=parts,
        dues=dues,
        receipt_date=today,
        student=student,
        collected_by=ctx.membership_id,
        collector_name=collector,
        actor_user_id=ctx.user_id,
    )
    links.close_overlapping(
        session, account.id, {p[0] for p in parts}, cancelled_by=ctx.membership_id
    )
    return receipt_out(session, row)


def write_receipt(
    session: Session,
    *,
    account: StudentFeeAccount,
    amount: Decimal,
    mode: str,
    details: dict[str, str],
    utr: str | None,
    parts: Sequence[tuple[int, uuid.UUID | None, Decimal]],
    dues: ledger.Dues,
    receipt_date: dt.date,
    student: StudentSummary,
    collected_by: uuid.UUID | None,
    collector_name: str | None,
    actor_user_id: uuid.UUID,
    provider_payment_id: str | None = None,
    payment_intent_id: uuid.UUID | None = None,
    system: bool = False,
) -> Receipt:
    """Write one receipt in the caller's transaction (counter or online, FR-FEE-010/011): the
    frozen content and its SHA-256, the number from the locked counter row LAST (gap-free), the
    allocations and the payment ledger entries, the audit event and the PDF job. The caller has
    locked the account, checked the day and computed ``parts`` from ``dues``."""
    heads = repo.heads_by_id(session)
    fee_settings = setup.settings_row(session)
    year = tenancy.get_academic_year(session, account.academic_year_id)
    lines = []
    for inst_no, head_id, part in parts:
        found = dues.line(inst_no, head_id)
        label = found[1].label if found else f"Instalment {inst_no}"
        head = heads.get(head_id) if head_id else None
        lines.append(
            {
                "instalment_no": inst_no,
                "label": label,
                "head_id": str(head_id) if head_id else None,
                "head_code": head.code if head else None,
                "head_name": head.name if head else ledger.LATE_FEE_NAME,
                "amount": f"{part:.2f}",
                "amount_text": _amount_text(part),
            }
        )
    financial_year = c.fy(receipt_date)
    numbering = c.settings().numbering.receipt
    # Last: the counter row stays locked until commit (gap-free, FR-FEE-010).
    number = repo.allocate_number(session, numbering.series, financial_year)
    receipt_number = numbering.render(financial_year, number)
    content: dict[str, Any] = {
        "school": _school(session),
        "receipt_number": receipt_number,
        "financial_year": financial_year,
        "receipt_date": receipt_date.isoformat(),
        "academic_year": year.label,
        "student": {
            "id": str(account.student_id),
            "name": student.display_name or "—",
            "admission_no": student.admission_no or "—",
            "class_section": student.class_section or "—",
        },
        "lines": lines,
        "amount": f"{amount:.2f}",
        "amount_text": _amount_text(amount),
        "amount_in_words": in_words(amount),
        "mode": mode,
        "mode_details": details,
        "utr": utr,
        "collected_by_name": collector_name,
        "gst_note": fee_settings.gst_note,
        "paper_size": fee_settings.paper_size,
        "template_version": c.settings().receipt.template_version,
    }
    receipt_id = new_id()
    tenant_id = account.tenant_id
    with c.db_errors():
        row = repo.insert_receipt(
            session,
            id=receipt_id,
            tenant_id=tenant_id,
            account_id=account.id,
            financial_year=financial_year,
            receipt_no=number,
            receipt_number=receipt_number,
            receipt_date=receipt_date,
            amount=amount,
            mode=mode,
            mode_details=details,
            utr=utr,
            provider_payment_id=provider_payment_id,
            payment_intent_id=payment_intent_id,
            collected_by=collected_by,
            issued_at=c.now(session),
            content=content,
            content_sha256=content_digest(content),
        )
        repo.insert_allocations(
            session,
            [
                {
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "receipt_id": receipt_id,
                    "instalment_no": inst_no,
                    "head_id": head_id,
                    "amount": part,
                }
                for inst_no, head_id, part in parts
            ],
        )
        repo.insert_entries(
            session,
            [
                {
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "account_id": account.id,
                    "entry_kind": "payment",
                    "amount": -part,
                    "instalment_no": inst_no,
                    "head_id": head_id,
                    "entry_date": receipt_date,
                    "receipt_id": receipt_id,
                    "posted_by": collected_by,
                }
                for inst_no, head_id, part in parts
            ],
        )
    summary: dict[str, Any] = {
        "receipt_number": receipt_number,
        "mode": mode,
        "amount": c.money_text(amount),
        "account_id": account.id,
        "student_id": account.student_id,
        "lines": len(parts),
    }
    if payment_intent_id is not None:
        summary["intent_id"] = payment_intent_id
    c.record(
        session,
        action="fee.receipt.issued",
        resource_type="fee_receipt",
        resource_id=row.id,
        summary=summary,
        system=system,
    )
    ops.enqueue_event(
        session,
        RENDER_EVENT,
        {"receipt_id": str(row.id), "user_id": str(actor_user_id), "account_id": str(account.id)},
    )
    log.info("fee.receipt.issued", resource_type="fee_receipt", resource_id=row.id)
    return row


# --- reads ---------------------------------------------------------------------------------------


def load_receipt(session: Session, receipt_id: uuid.UUID, *, lock: bool = False) -> Receipt:
    row = repo.get_receipt(session, receipt_id, lock=lock)
    if row is None:
        raise NotFound("Receipt not found")
    return row


def cancellation_out(
    row: ReceiptCancellation, number: str, names: dict[uuid.UUID, str]
) -> CancellationOut:
    return CancellationOut(
        id=row.id,
        receipt_id=row.receipt_id,
        receipt_number=number,
        reason_code=row.reason_code,
        reason=row.reason,
        status=row.status,
        requested_by=row.requested_by,
        requested_by_name=names.get(row.requested_by),
        requested_at=row.requested_at,
        decided_by=row.decided_by,
        decided_by_name=names.get(row.decided_by) if row.decided_by else None,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        cancelled_on=row.cancelled_on,
        version=row.version,
    )


def refund_out(row: Refund, number: str, names: dict[uuid.UUID, str]) -> RefundOut:
    return RefundOut(
        id=row.id,
        receipt_id=row.receipt_id,
        receipt_number=number,
        account_id=row.account_id,
        amount=row.amount,
        mode=row.mode,
        reason=row.reason,
        status=row.status,
        requested_by=row.requested_by,
        requested_by_name=names.get(row.requested_by),
        requested_at=row.requested_at,
        decided_by=row.decided_by,
        decided_by_name=names.get(row.decided_by) if row.decided_by else None,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        voucher_number=row.voucher_number,
        voucher_date=row.voucher_date,
        version=row.version,
    )


def refunded_amount(refunds: Sequence[Refund]) -> Decimal:
    return total(r.amount for r in refunds if r.status == "processed")


def committed_refunds(refunds: Sequence[Refund]) -> Decimal:
    """Refunds that hold part of the receipt (asked for, approved, in progress or paid)."""
    return total(r.amount for r in refunds if r.status in LIVE_REFUNDS)


def receipt_out(session: Session, row: Receipt) -> ReceiptOut:
    content = row.content
    cancellations = repo.cancellations_of(session, [row.id])
    refunds = repo.refunds_of(session, [row.id])
    live = next((x for x in reversed(cancellations) if x.status in ("pending", "approved")), None)
    people = c.unique_ids(
        [row.collected_by]
        + [x.requested_by for x in cancellations]
        + [x.decided_by for x in cancellations]
        + [x.requested_by for x in refunds]
        + [x.decided_by for x in refunds]
    )
    names = identity.member_display_names(session, people)
    student = content.get("student") or {}
    account = repo.get_account(session, row.account_id)
    return ReceiptOut(
        id=row.id,
        receipt_number=row.receipt_number,
        financial_year=row.financial_year,
        receipt_date=row.receipt_date,
        amount=row.amount,
        amount_in_words=str(content.get("amount_in_words", in_words(row.amount))),
        mode=row.mode,
        mode_details={str(k): str(v) for k, v in (row.mode_details or {}).items()},
        utr=row.utr,
        status=row.status,
        account_id=row.account_id,
        student_id=account.student_id if account else uuid.UUID(str(student.get("id"))),
        student_name=str(student.get("name", "")),
        class_section=str(student.get("class_section", "")),
        admission_no=str(student.get("admission_no", "")),
        collected_by=row.collected_by,
        collected_by_name=names.get(row.collected_by) if row.collected_by else None,
        issued_at=row.issued_at,
        lines=[
            ReceiptLineOut(
                instalment_no=int(line["instalment_no"]),
                label=str(line["label"]),
                head_code=line.get("head_code"),
                head_name=str(line["head_name"]),
                amount=Decimal(str(line["amount"])),
            )
            for line in content.get("lines") or []
        ],
        content_sha256=bytes(row.content_sha256).hex(),
        pdf_status=row.pdf_status,
        cancelled_on=live.cancelled_on if live and live.status == "approved" else None,
        cancellation=cancellation_out(live, row.receipt_number, names) if live else None,
        refunded=refunded_amount(refunds),
        refunds=[refund_out(r, row.receipt_number, names) for r in refunds],
        version=row.version,
        bank_confirmed_at=(
            upi_repo.confirmation_times(session, [row.id]).get(row.id)
            if row.mode == "upi_qr"
            else None
        ),
    )


def get_receipt(session: Session, ctx: UserContext, receipt_id: uuid.UUID) -> ReceiptOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    return receipt_out(session, load_receipt(session, receipt_id))


def _after(cursor: str | None) -> tuple[dt.datetime, uuid.UUID] | None:
    data = decode_cursor(cursor)
    if data is None:
        return None
    try:
        return dt.datetime.fromisoformat(str(data["t"])), uuid.UUID(str(data["i"]))
    except (KeyError, ValueError) as exc:
        raise ValidationFailed([c.error("cursor", "invalid")]) from exc


def list_receipts(
    session: Session,
    ctx: UserContext,
    *,
    receipt_date: dt.date | None,
    mode: str | None,
    collected_by: uuid.UUID | None,
    status: str | None,
    account_id: uuid.UUID | None,
    limit: int,
    cursor: str | None,
) -> Page[ReceiptListItem]:
    """Receipts newest first; filters are ids and codes only (docs/17 §5.8)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    rows = repo.list_receipts(
        session,
        receipt_date=receipt_date,
        mode=mode,
        collected_by=collected_by,
        status=status,
        account_id=account_id,
        after=_after(cursor),
        limit=limit + 1,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    ids = [r.id for r in rows]
    cancellations = repo.cancellations_of(session, ids)
    refunds = repo.refunds_of(session, ids)
    names = identity.member_display_names(session, c.unique_ids(r.collected_by for r in rows))
    accounts = repo.accounts_by_id(session, {r.account_id for r in rows})
    items = []
    for r in rows:
        mine_c = [x for x in cancellations if x.receipt_id == r.id]
        approved = next((x for x in mine_c if x.status == "approved"), None)
        student = r.content.get("student") or {}
        items.append(
            ReceiptListItem(
                id=r.id,
                receipt_number=r.receipt_number,
                receipt_date=r.receipt_date,
                amount=r.amount,
                mode=r.mode,
                status=r.status,
                account_id=r.account_id,
                student_id=accounts[r.account_id].student_id,
                student_name=str(student.get("name", "")),
                class_section=str(student.get("class_section", "")),
                collected_by=r.collected_by,
                collected_by_name=names.get(r.collected_by) if r.collected_by else None,
                cancelled_on=approved.cancelled_on if approved else None,
                cancellation_pending=any(x.status == "pending" for x in mine_c),
                refunded=refunded_amount([x for x in refunds if x.receipt_id == r.id]),
            )
        )
    last = rows[-1] if rows else None
    next_cursor = (
        encode_cursor({"t": last.issued_at.isoformat(), "i": str(last.id)})
        if more and last
        else None
    )
    return Page[ReceiptListItem](data=items, next_cursor=next_cursor)


def cancelled_on(session: Session, row: Receipt) -> dt.date | None:
    for x in repo.cancellations_of(session, [row.id]):
        if x.status == "approved":
            return x.cancelled_on
    return None


def print_page(session: Session, ctx: UserContext, receipt_id: uuid.UUID) -> str:
    """The print view (HTML, no scripts) of a receipt; a cancelled one is marked CANCELLED.
    Audit ``fee.receipt.print_viewed``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    row = load_receipt(session, receipt_id)
    page = templates.render_receipt(row.content, cancelled_on=cancelled_on(session, row))
    c.record(
        session,
        action="fee.receipt.print_viewed",
        resource_type="fee_receipt",
        resource_id=row.id,
        summary={"receipt_number": row.receipt_number, "status": row.status},
    )
    return page


def download_url(session: Session, ctx: UserContext, receipt_id: uuid.UUID) -> ReceiptDownloadOut:
    """A presigned PDF download (≤ 5 minutes) once stored. 409 ``pdf_not_ready`` before; 409
    ``receipt_cancelled`` for a cancelled receipt (its stored PDF predates the cancellation; the
    print view shows it marked). Audit ``fee.receipt.downloaded``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    row = load_receipt(session, receipt_id)
    if row.status == "cancelled":
        raise ReceiptCancelled()
    if row.document_id is None:
        raise Conflict("The PDF is still being made. Try again in a moment.", code="pdf_not_ready")
    filename = _FILENAME_RE.sub("-", f"receipt-{row.receipt_number}") + ".pdf"
    url, expires_at = documents.generated_download_url(
        session,
        row.document_id,
        filename=filename,
        ttl_s=c.settings().receipt.download_url_ttl_s,
    )
    c.record(
        session,
        action="fee.receipt.downloaded",
        resource_type="fee_receipt",
        resource_id=row.id,
        summary={"receipt_number": row.receipt_number, "document_id": row.document_id},
    )
    return ReceiptDownloadOut(url=url, expires_at=expires_at, filename=filename)


def retry_render(session: Session, ctx: UserContext, receipt_id: uuid.UUID) -> ReceiptOut:
    """Queue the PDF again after it failed (``fee.collect``); idempotent otherwise."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT)
    row = load_receipt(session, receipt_id, lock=True)
    if row.pdf_status == "failed":
        with c.db_errors():
            row = repo.update_receipt(session, row.id, {"pdf_status": "queued", "pdf_error": None})
        ops.enqueue_event(
            session,
            RENDER_EVENT,
            {
                "receipt_id": str(row.id),
                "user_id": str(ctx.user_id),
                "account_id": str(row.account_id),
            },
        )
        c.record(
            session,
            action="fee.receipt.render_requested",
            resource_type="fee_receipt",
            resource_id=row.id,
            summary={"receipt_number": row.receipt_number},
        )
    return receipt_out(session, row)


# --- PDF (worker, queue "pdf") -------------------------------------------------------------------


def render_pdf(
    tenant_id: uuid.UUID,
    receipt_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    renderer: PdfRenderer | None = None,
) -> str:
    """Worker: print a receipt's frozen content to PDF and store it as a ``fee_receipt``
    document (FR-FEE-011). Idempotent (a stored PDF is left alone). Errors propagate for a retry;
    after the last one the task calls :func:`mark_pdf_failed`."""
    with tenant_session(tenant_id, user_id) as session:
        row = repo.get_receipt(session, receipt_id)
        if row is None:
            return "missing"
        if row.document_id is not None:
            return row.pdf_status
        page = templates.render_receipt(row.content, for_pdf=True)
        title = f"Fee receipt {row.receipt_number}"
        issued_on = row.receipt_date
    pdf = (renderer or get_renderer()).render(page)
    with tenant_session(tenant_id, user_id) as session:
        row = repo.get_receipt(session, receipt_id, lock=True)
        if row is None:
            return "missing"
        if row.document_id is not None:
            return row.pdf_status
        document_id = documents.store_generated_document(
            session,
            purpose=DOCUMENT_PURPOSE,
            title=title[:200],
            content=pdf,
            created_by=user_id,
            acl_roles=c.settings().receipt.document_acl_roles,
            issued_on=issued_on,
            language="en",
        )
        with c.db_errors():
            row = repo.update_receipt(
                session,
                row.id,
                {"document_id": document_id, "pdf_status": "ready", "pdf_error": None},
            )
        if row.status == "cancelled":
            documents.archive_generated_document(session, document_id)
        c.record(
            session,
            action="fee.receipt.pdf_stored",
            resource_type="fee_receipt",
            resource_id=row.id,
            summary={"document_id": document_id, "size_bytes": len(pdf)},
            system=True,
        )
    log.info("fee.receipt.pdf_stored", resource_type="fee_receipt", resource_id=receipt_id)
    return "ready"


def mark_pdf_failed(tenant_id: uuid.UUID, receipt_id: uuid.UUID, code: str) -> None:
    with tenant_session(tenant_id) as session:
        row = repo.get_receipt(session, receipt_id, lock=True)
        if row is None or row.document_id is not None:
            return
        repo.update_receipt(session, row.id, {"pdf_status": "failed", "pdf_error": code})
        c.record(
            session,
            action="fee.receipt.pdf_failed",
            resource_type="fee_receipt",
            resource_id=row.id,
            summary={"error_code": code},
            system=True,
        )


__all__ = [
    "RENDER_EVENT",
    "RENDER_TASK",
    "AmountExceedsDue",
    "HeadNotCollectable",
    "ReceiptCancelled",
    "UtrInUse",
    "content_digest",
    "download_url",
    "get_receipt",
    "issue_receipt",
    "list_receipts",
    "mark_pdf_failed",
    "print_page",
    "receipt_out",
    "render_pdf",
    "retry_render",
]
