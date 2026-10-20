"""What the parent receipt and dues pages may read from fees (R2-3H; ADR-0046 decisions 2 and 9;
FR-PUB-009, FR-PAY-005; US-2402, US-2403).

No permission check here: the only caller is ``app.public.pages``, which reached the receipt or
the fee account through a live parent link of this school (``tenant_session``, RLS) and checked
the link's guardian against the student. Every function returns an explicit allowlist of fields
(frozen dataclasses), never a row: no phone, address, date of birth, other children, ledger
history or staff names.

- :func:`receipt` reads the receipt's frozen content (number, date, child's name, class and
  admission number, amounts per head, mode) and "Cancelled on ..." (FR-PUB-007: a cancelled
  receipt's link keeps working and says so). Shown even while fees are switched off: it is a
  record of money the school took.
- :func:`receipt_pdf` is the presigned PDF (at most 5 minutes; none for a cancelled receipt,
  whose stored PDF predates the cancellation). Audit ``fee.receipt.downloaded`` (system,
  ``via: parent_link``).
- :func:`dues` is what the account owes now per instalment (collectable heads only; taxable heads
  are invoiced in Tally, FR-FEE-023), the late fee shown separately, only while fees are on.
- :func:`pay` creates or reuses the payment link for the chosen instalments (``created_via
  parent_page``, the link's handle recorded on the intent) and says whether it was reused, so
  the page's ``public_pay`` budget counts only new links (ADR-0046 decision 9).
- :func:`payment` is the status of one intent of the account (the Razorpay return page), or of
  the newest intent made from the handle.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.documents import service as documents
from app.fees import _common as c
from app.fees import ledger, online, receipts
from app.fees import online_repository as orepo
from app.fees import repository as repo
from app.fees.money import ZERO
from app.fees.online_schemas import PaymentLinkInfo
from app.fees.schemas import DueLine


@dataclass(frozen=True, slots=True)
class ReceiptLine:
    label: str
    head_name: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class ParentReceipt:
    """ADR-0046 decision 2 "Receipt" (owner decision D22). ``student_id`` is for the caller's
    guardian check only and is never sent to the parent."""

    student_id: uuid.UUID
    receipt_number: str
    receipt_date: dt.date
    student_name: str
    class_section: str
    admission_no: str
    lines: tuple[ReceiptLine, ...]
    amount: Decimal
    amount_in_words: str
    mode: str
    cancelled_on: dt.date | None
    pdf_ready: bool


@dataclass(frozen=True, slots=True)
class DueInstalment:
    instalment_no: int
    label: str
    due_on: dt.date | None
    due: Decimal
    late_fee: Decimal
    overdue: bool


@dataclass(frozen=True, slots=True)
class ParentDues:
    """ADR-0046 decision 2 "Dues". ``student_id`` is for the caller's checks only."""

    student_id: uuid.UUID
    instalments: tuple[DueInstalment, ...]
    total_due: Decimal
    late_fee_total: Decimal
    as_of: dt.date


@dataclass(frozen=True, slots=True)
class ParentPayment:
    """A payment link for the parent's redirect; ``reused``: an open link for the same
    instalments and amount already existed (no new link, no budget spent)."""

    link: PaymentLinkInfo
    reused: bool


@dataclass(frozen=True, slots=True)
class ParentPaymentStatus:
    status: str
    amount: Decimal
    receipt_issued: bool


def receipt(session: Session, receipt_id: uuid.UUID) -> ParentReceipt | None:
    row = repo.get_receipt(session, receipt_id)
    if row is None:
        return None
    account = repo.get_account(session, row.account_id)
    if account is None:
        return None
    content = row.content
    student = content.get("student") or {}
    return ParentReceipt(
        student_id=account.student_id,
        receipt_number=row.receipt_number,
        receipt_date=row.receipt_date,
        student_name=str(student.get("name", "")),
        class_section=str(student.get("class_section", "")),
        admission_no=str(student.get("admission_no", "")),
        lines=tuple(
            ReceiptLine(
                label=str(line["label"]),
                head_name=str(line["head_name"]),
                amount=Decimal(str(line["amount"])),
            )
            for line in content.get("lines") or []
        ),
        amount=row.amount,
        amount_in_words=str(content.get("amount_in_words", "")),
        mode=row.mode,
        cancelled_on=receipts.cancelled_on(session, row),
        pdf_ready=row.status != "cancelled" and row.document_id is not None,
    )


def receipt_pdf(session: Session, receipt_id: uuid.UUID) -> tuple[str, dt.datetime] | None:
    row = repo.get_receipt(session, receipt_id)
    if row is None or row.status == "cancelled" or row.document_id is None:
        return None
    filename = f"receipt-{row.receipt_number}".replace("/", "-") + ".pdf"
    try:
        url, expires_at = documents.generated_download_url(
            session,
            row.document_id,
            filename=filename,
            ttl_s=c.settings().receipt.download_url_ttl_s,
        )
    except (Conflict, NotFound):  # not scanned yet, or gone: the page says "not ready"
        return None
    c.record(
        session,
        action="fee.receipt.downloaded",
        resource_type="fee_receipt",
        resource_id=row.id,
        summary={"document_id": row.document_id, "via": "parent_link"},
        system=True,
    )
    return url, expires_at


def account_student(session: Session, account_id: uuid.UUID) -> uuid.UUID | None:
    account = repo.get_account(session, account_id)
    return account.student_id if account is not None else None


def dues(session: Session, account_id: uuid.UUID) -> ParentDues | None:
    """``None`` while fees are off, for an unknown or closed account."""
    if not c.fees_on(session, repo.current_tenant_id(session)):
        return None
    account = repo.get_account(session, account_id)
    if account is None or account.status != "open":
        return None
    today = c.today(session)
    found = ledger.dues_for(session, account, today)
    per: dict[int, list[DueLine]] = {}
    for _, line in found.lines:
        if line.collectable:
            per.setdefault(line.instalment_no, []).append(line)
    out: list[DueInstalment] = []
    for no in sorted(per):
        lines = per[no]
        due = sum((x.due for x in lines), ZERO)
        if due <= 0:
            continue
        late = sum((x.due for x in lines if x.head_id is None and x.due > 0), ZERO)
        out.append(
            DueInstalment(
                instalment_no=no,
                label=lines[0].label,
                due_on=lines[0].due_on,
                due=due,
                late_fee=late,
                overdue=any(x.overdue for x in lines),
            )
        )
    return ParentDues(
        student_id=account.student_id,
        instalments=tuple(out),
        total_due=sum((i.due for i in out), ZERO),
        late_fee_total=sum((i.late_fee for i in out), ZERO),
        as_of=today,
    )


def pay(
    session: Session,
    account_id: uuid.UUID,
    instalments: Collection[int],
    *,
    public_handle_id: uuid.UUID,
) -> ParentPayment:
    """Errors as :func:`app.fees.service.create_payment_intent` (404 ``module_hidden``; 409
    ``payments_not_connected``, ``nothing_due``, ``account_closed``, ``too_many_links``; 422
    ``instalments``; 502 ``payment_link_failed``)."""
    if not c.fees_on(session, repo.current_tenant_id(session)):
        raise c.ModuleHidden()
    intent, reused = online.create_intent(
        session,
        account_id=account_id,
        instalments=instalments,
        created_via="parent_page",
        created_by=None,
        public_handle_id=public_handle_id,
    )
    info = online.link_info(session, intent.id)
    if info is None:  # pragma: no cover - the intent was just read or written
        raise RuntimeError("intent missing")
    return ParentPayment(link=info, reused=reused)


def payment(
    session: Session,
    account_id: uuid.UUID,
    *,
    intent_id: uuid.UUID | None,
    public_handle_id: uuid.UUID,
) -> ParentPaymentStatus | None:
    """The intent ``intent_id`` when it belongs to the account (Razorpay's return carries its
    reference), else the newest one the handle made; ``None`` otherwise."""
    if intent_id is not None:
        intent = orepo.get_intent(session, intent_id)
        if intent is None or intent.account_id != account_id:
            return None
    else:
        intent = orepo.latest_intent_of_handle(session, public_handle_id)
        if intent is None or intent.account_id != account_id:
            return None
    info = online.link_info(session, intent.id)
    if info is None:  # pragma: no cover
        return None
    return ParentPaymentStatus(
        status=info.status,
        amount=info.amount,
        receipt_issued=orepo.receipt_for_intent(session, intent.id) is not None,
    )


__all__ = [
    "DueInstalment",
    "ParentDues",
    "ParentPayment",
    "ParentPaymentStatus",
    "ParentReceipt",
    "ReceiptLine",
    "account_student",
    "dues",
    "pay",
    "payment",
    "receipt",
    "receipt_pdf",
]
