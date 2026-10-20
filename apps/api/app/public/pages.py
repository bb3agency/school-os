"""Parent page content (wave R2-3H; ADR-0046 decisions 2, 5 and 9; FR-PUB-007..009, FR-PAY-005;
US-2401..US-2405).

Assemblers for ``/public/links/current/*`` (``app.public.api``). Each one runs in the link's
school's ``tenant_session`` (RLS) on a handle already re-read by
:func:`app.public.service.load_handle`, and reads ONLY through other modules' public services:
``circulars.service`` (the notice), ``fees.service`` (receipt, dues, payment links, UPI),
``students.service`` (the guardian's links, the child's first name and class, the guardian's
language) and ``messaging.service`` (the opt-in state, the office phone).

The page shows exactly ADR-0046 decision 2 for its kind; every response is an explicit allowlist
(``app.public.schemas``). Anything that does not open a page raises
:class:`app.public.guards.PublicNotFound`, the same 404 as an unknown key, so a caller learns
nothing about a school, a child or a guardian:

- the handle's ``target_type`` is not the one of its kind (``PARENT_TARGET_TYPES``);
- receipt, dues and opt-in links without a guardian, or whose guardian is no longer linked to
  the receipt's or account's student (BOLA across guardians; FR-PUB-007);
- a notice that is not approved, an unknown receipt, a closed account, fees switched off (dues),
  WhatsApp messages not offered here (opt-in).

Nothing here logs a name, amount, phone or key (invariant 5); audit events are written by the
owning services (``notice.downloaded``, ``fee.receipt.downloaded``, ``fee.payment_link.created``,
``guardian.updated``) in the same transaction.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.circulars import service as circulars
from app.core.errors import DomainError
from app.core.languages import Language
from app.fees import service as fees
from app.messaging import service as messaging
from app.public.config import PARENT_TARGET_TYPES, ParentKind, is_parent_kind, rules
from app.public.guards import PublicNotFound
from app.public.schemas import (
    LanguageChoice,
    ParentDocumentLinkOut,
    ParentDueInstalmentOut,
    ParentDuesOut,
    ParentNoticeOut,
    ParentOptinCategoryOut,
    ParentOptinOut,
    ParentPaymentStatusOut,
    ParentPayOut,
    ParentReceiptLineOut,
    ParentReceiptOut,
    ParentUpiOut,
)
from app.public.service import HandleView
from app.students import service as students
from app.tenancy import service as tenancy


class PaymentLinkRefused(DomainError):
    """The provider answered with a link outside ``pay_redirect_hosts`` (fail closed)."""

    status, code, title = 502, "payment_link_failed", "The payment page could not be opened"


def target(handle: HandleView, kind: ParentKind) -> uuid.UUID:
    """The handle's target id when it is a ``kind`` link pointing at the right type, else 404."""
    if handle.kind != kind or not is_parent_kind(handle.kind):
        raise PublicNotFound()
    if handle.target_type != PARENT_TARGET_TYPES[kind]:
        raise PublicNotFound()
    return handle.target_id


def _guardian_of(session: Session, handle: HandleView, student_id: uuid.UUID) -> uuid.UUID:
    """The link's guardian, still linked to ``student_id`` (else 404)."""
    guardian_id = handle.guardian_id
    if guardian_id is None:
        raise PublicNotFound()
    if student_id not in students.guardian_student_ids(session, guardian_id):
        raise PublicNotFound()
    return guardian_id


# --- notice -------------------------------------------------------------------------------------


def notice(session: Session, handle: HandleView, language: Language) -> ParentNoticeOut:
    found = circulars.parent_notice(session, target(handle, "parent_notice"), language)
    if found is None:
        raise PublicNotFound()
    return ParentNoticeOut(
        title=found.title, body=found.body, date=found.approved_on, has_pdf=found.pdf_available
    )


# --- receipt ------------------------------------------------------------------------------------


def _receipt(session: Session, handle: HandleView) -> fees.ParentReceipt:
    found = fees.parent_receipt(session, target(handle, "parent_receipt"))
    if found is None:
        raise PublicNotFound()
    _guardian_of(session, handle, found.student_id)
    return found


def receipt(session: Session, handle: HandleView) -> ParentReceiptOut:
    found = _receipt(session, handle)
    return ParentReceiptOut(
        receipt_number=found.receipt_number,
        receipt_date=found.receipt_date,
        student_name=found.student_name,
        class_section=found.class_section,
        admission_no=found.admission_no,
        lines=[
            ParentReceiptLineOut(label=x.label, head_name=x.head_name, amount=x.amount)
            for x in found.lines
        ],
        amount=found.amount,
        amount_in_words=found.amount_in_words,
        mode=found.mode,
        cancelled_on=found.cancelled_on,
        has_pdf=found.pdf_ready,
    )


# --- dues and paying ----------------------------------------------------------------------------


def _account(session: Session, handle: HandleView) -> tuple[uuid.UUID, uuid.UUID]:
    """``(account id, student id)`` of a dues link whose guardian is still the child's."""
    account_id = target(handle, "parent_dues")
    student_id = fees.parent_account_student(session, account_id)
    if student_id is None:
        raise PublicNotFound()
    _guardian_of(session, handle, student_id)
    return account_id, student_id


def dues(session: Session, handle: HandleView) -> ParentDuesOut:
    account_id, student_id = _account(session, handle)
    found = fees.parent_dues(session, account_id)
    child = students.parent_page_child(session, student_id)
    if found is None or child is None:
        raise PublicNotFound()
    upi = fees.parent_upi_qr(session)
    return ParentDuesOut(
        child_first_name=child.first_name,
        class_name=child.class_name,
        as_of=found.as_of,
        instalments=[
            ParentDueInstalmentOut(
                instalment_no=i.instalment_no,
                label=i.label,
                due_on=i.due_on,
                due=i.due,
                late_fee=i.late_fee,
                overdue=i.overdue,
            )
            for i in found.instalments
        ],
        total_due=found.total_due,
        late_fee_total=found.late_fee_total,
        pay_online=bool(found.instalments) and fees.payments_connected(session),
        upi=ParentUpiOut(payee_name=upi[0], upi_id=upi[1], qr_svg=upi[2]) if upi else None,
        office_phone=messaging.office_phone(session),
    )


def pay(session: Session, handle: HandleView, instalments: list[int]) -> ParentPayOut:
    """Create or reuse the payment link for the ticked instalments (ADR-0046 decision 9).
    ``reused`` tells the route not to spend the ``public_pay`` budget. Errors from
    ``fees.service`` pass through (409 ``payments_not_connected``, ``nothing_due``,
    ``too_many_links``; 422 ``instalments``; 502 ``payment_link_failed``); fees switched off is
    the uniform 404."""
    account_id, _ = _account(session, handle)
    if fees.parent_dues(session, account_id) is None:
        raise PublicNotFound()
    made = fees.parent_pay(session, account_id, instalments, public_handle_id=handle.handle_id)
    parts = urlsplit(made.link.short_url)
    if parts.scheme != "https" or not rules().pay_redirect_allowed(parts.hostname):
        raise PaymentLinkRefused()
    return ParentPayOut(
        redirect_url=made.link.short_url, amount=made.link.amount, reused=made.reused
    )


def payment(session: Session, handle: HandleView, intent_ref: str | None) -> ParentPaymentStatusOut:
    """The status of the payment link Razorpay returned from (``intent_ref`` = its reference,
    the intent id as 32 hex characters), else of the newest link this dues link made."""
    account_id, _ = _account(session, handle)
    intent_id: uuid.UUID | None = None
    if intent_ref is not None:
        try:
            intent_id = uuid.UUID(hex=intent_ref)
        except ValueError:
            raise PublicNotFound() from None
    found = fees.parent_payment(
        session, account_id, intent_id=intent_id, public_handle_id=handle.handle_id
    )
    if found is None:
        raise PublicNotFound()
    status = found.status
    if status not in ("created", "paid", "expired", "cancelled", "failed"):  # pragma: no cover
        raise PublicNotFound()
    return ParentPaymentStatusOut(
        status=status,
        amount=found.amount,
        receipt_issued=found.receipt_issued,
    )


# --- documents ----------------------------------------------------------------------------------


def document(session: Session, handle: HandleView) -> ParentDocumentLinkOut:
    """The notice's or receipt's PDF (presigned, at most 5 minutes), else 404."""
    found: tuple[object, object] | None
    if handle.kind == "parent_notice":
        found = circulars.parent_notice_pdf(session, target(handle, "parent_notice"))
    elif handle.kind == "parent_receipt":
        _receipt(session, handle)
        found = fees.parent_receipt_pdf(session, target(handle, "parent_receipt"))
    else:
        raise PublicNotFound()
    if found is None:
        raise PublicNotFound()
    url, expires_at = found
    return ParentDocumentLinkOut.model_validate({"url": url, "expires_at": expires_at})


# --- opt-in -------------------------------------------------------------------------------------


def _optin_guardian(handle: HandleView) -> uuid.UUID:
    guardian_id = target(handle, "parent_optin")
    if handle.guardian_id is None or handle.guardian_id != guardian_id:
        raise PublicNotFound()
    return guardian_id


def optin(session: Session, handle: HandleView) -> ParentOptinOut:
    guardian_id = _optin_guardian(handle)
    states = messaging.parent_optin_state(session, guardian_id)
    if states is None:
        raise PublicNotFound()
    own = students.guardian_language(session, guardian_id)
    return ParentOptinOut(
        categories=[
            ParentOptinCategoryOut.model_validate(
                {
                    "category": s.category,
                    "status": s.status,
                    "stopped_by_parent": s.stopped_by_parent,
                }
            )
            for s in states
        ],
        language_choice=own if own is not None else "school",
        school_language=tenancy.parent_language(session),
    )


def set_language(session: Session, handle: HandleView, choice: LanguageChoice) -> ParentOptinOut:
    """The parent's message language (ADR-0044 decision 4; FR-LANG-003). Never processed while
    the school is suspended (owner decision D31: the guard's resolver already refuses an opt-in
    link of a suspended school)."""
    guardian_id = _optin_guardian(handle)
    if messaging.parent_optin_state(session, guardian_id) is None:
        raise PublicNotFound()
    language: Language | None = None if choice == "school" else choice
    students.set_guardian_language_from_parent_page(
        session, guardian_id, language, handle_id=handle.handle_id
    )
    return optin(session, handle)


__all__ = [
    "PaymentLinkRefused",
    "document",
    "dues",
    "notice",
    "optin",
    "pay",
    "payment",
    "receipt",
    "set_language",
    "target",
]
