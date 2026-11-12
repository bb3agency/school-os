"""Family counter: siblings and one payment across their accounts (FM-07; FR-FEE-101..112;
internal: other modules use ``app.fees.service``).

- **Siblings (FR-FEE-101).** Students who share a guardian with the chosen student
  (``sis.student_guardians``; a link is made by staff on the student's record, never inferred
  from a phone number). Only students the clerk may see are shown (``students.summaries`` under
  the clerk's scope, inv. 3); nobody outside it is named or counted.
- **One payment, one receipt per account (FR-FEE-103..106).** The receipt design is one account
  per receipt (``fin.receipts.account_id``), so a family payment issues one ordinary counter
  receipt per account in **one transaction**: accounts locked in id order, the day checked once,
  every part allocated and checked before the first number is taken, then each receipt written
  through :func:`receipts.write_receipt` (frozen content, SHA-256, gap-free number from the
  locked counter, allocations, payment ledger entries, its own ``fee.receipt.issued`` audit and
  PDF job). Numbers are consecutive; any failure rolls back every receipt (no gap). The ledger
  stays append-only and nothing about a receipt changes.
- **No UPI (FR-FEE-104).** A UTR belongs to one receipt (FR-FEE-014), so UPI is taken per child.
- **Hand-over slip (FR-FEE-107).** One A4 page listing the receipts given to the parent, read
  from each receipt's frozen content.
- **Out of scope.** No sibling concession is suggested or applied (owner question of FM-07): a
  sibling concession stays a hand-entered maker-checker request. No family payment link.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.logging import get_logger
from app.fees import _common as c
from app.fees import arrears, ledger, links, receipts, templates
from app.fees.arrears_schemas import YearDue
from app.fees.family_schemas import (
    FamilyCounterOut,
    FamilyMember,
    FamilyPaymentCreate,
    FamilyPaymentOut,
)
from app.fees.money import ZERO, group_indian, in_words, total
from app.fees.schemas import ReceiptCreate
from app.identity import service as identity
from app.students import service as students

log = get_logger(__name__)

CONFIG_PATH: Final = Path(__file__).with_name("family.yaml")


class FamilyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    max_accounts: int = Field(ge=2, le=50)
    modes: tuple[str, ...] = Field(min_length=1)


@lru_cache(maxsize=1)
def load_config() -> FamilyConfig:
    cfg = FamilyConfig.model_validate(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))
    if "upi_qr" in cfg.modes or set(cfg.modes) - {"cash", "cheque", "bank_transfer"}:
        raise ValueError("family payments take cash, cheque or bank transfer only")
    return cfg


class UpiOneReceiptPerUtr(ValidationFailed):
    def __init__(self) -> None:
        super().__init__(
            [c.error("mode", "upi_one_receipt_per_utr")],
            detail="A UPI reference (UTR) belongs to one receipt. Take each child's UPI payment "
            "with its own receipt.",
        )


# --- siblings (FR-FEE-101/102) -------------------------------------------------------------------


def _siblings(session: Session, student_id: uuid.UUID) -> dict[uuid.UUID, list[str]]:
    """Other students sharing a guardian with ``student_id`` -> the shared guardians'
    relationships to that student (system read; the caller filters by scope)."""
    out: dict[uuid.UUID, list[str]] = {}
    for brief in students.guardians_of_students(session, [student_id]):
        for other in students.guardian_student_ids(session, brief.guardian_id):
            if other == student_id:
                continue
            kinds = out.setdefault(other, [])
            if brief.relationship not in kinds:
                kinds.append(brief.relationship)
    return out


def family_counter(session: Session, ctx: UserContext, student_id: uuid.UUID) -> FamilyCounterOut:
    """The chosen student and each sibling the clerk may see, with their open accounts, earlier
    years first (``fee.collect`` or ``finance.read``, school-wide). 404 when the student is not
    visible to the clerk. Nothing is written."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    chosen = students.summaries(session, ctx, [student_id]).get(student_id)
    if chosen is None:
        raise NotFound("Student not found")
    siblings = _siblings(session, student_id)
    visible = students.summaries(session, ctx, list(siblings))
    today = c.today(session)
    found = arrears.years_in_view(session)
    years = found or arrears.Years(current_id=uuid.UUID(int=0), labels={}, starts={})
    order = [student_id, *sorted(visible, key=lambda s: (visible[s].display_name or "", str(s)))]
    accounts = (
        [a for a in arrears.accounts_of(session, years, order) if a.status == "open"]
        if found is not None
        else []
    )
    dues = ledger.dues_of(session, accounts, today)
    per: dict[uuid.UUID, list[YearDue]] = {sid: [] for sid in order}
    for a in sorted(accounts, key=lambda a: (years.order(a.academic_year_id), str(a.id))):
        per[a.student_id].append(
            YearDue(
                account_id=a.id,
                academic_year_id=a.academic_year_id,
                academic_year_label=years.labels.get(a.academic_year_id),
                earlier_year=years.earlier(a.academic_year_id),
                balance=dues[a.id].balance,
                overdue=dues[a.id].overdue,
                collectable=dues[a.id].collectable,
                oldest_due_on=dues[a.id].oldest_due_on,
            )
        )
    members = []
    for sid in order:
        summary = chosen if sid == student_id else visible[sid]
        rows = per[sid]
        members.append(
            FamilyMember(
                student_id=sid,
                display_name=summary.display_name,
                admission_no=summary.admission_no,
                class_section=summary.class_section,
                status=str(summary.status),
                selected=sid == student_id,
                linked_by=[] if sid == student_id else siblings[sid],
                accounts=rows,
                collectable=total(r.collectable for r in rows),
            )
        )
    cfg = load_config()
    return FamilyCounterOut(
        student_id=student_id,
        as_of=today,
        members=members,
        total_collectable=total(m.collectable for m in members),
        max_accounts=cfg.max_accounts,
        modes=list(cfg.modes),
    )


# --- one payment, one receipt per account (FR-FEE-103..106) --------------------------------------


def _check_parts(data: FamilyPaymentCreate) -> None:
    cfg = load_config()
    if data.mode == "upi_qr":
        raise UpiOneReceiptPerUtr()
    if data.mode not in cfg.modes:
        raise ValidationFailed([c.error("mode", "invalid")])
    if len(data.parts) > cfg.max_accounts:
        raise ValidationFailed([c.error("parts", "too_many")])
    seen: set[uuid.UUID] = set()
    for i, part in enumerate(data.parts):
        if part.account_id in seen:
            raise ValidationFailed([c.error(f"parts.{i}.account_id", "duplicate")])
        seen.add(part.account_id)
        if part.amount > c.settings().limits.max_amount:
            raise ValidationFailed([c.error(f"parts.{i}.amount", "too_large")])


def issue_family_payment(
    session: Session, ctx: UserContext, data: FamilyPaymentCreate
) -> FamilyPaymentOut:
    """Take one payment for several siblings' accounts (``fee.collect``, school-wide,
    ``Idempotency-Key``): one receipt per account, all or none. Errors: 422 ``mode``
    ``upi_one_receipt_per_utr``; 422 ``parts`` (``too_many``, ``duplicate``,
    ``not_in_family``) and the mode-detail and allocation codes of a counter receipt; 409
    ``amount_exceeds_due``, ``head_not_collectable``, ``day_closed``, ``account_closed``; 404
    for an account the clerk cannot see. Audit: each receipt's ``fee.receipt.issued`` and one
    ``fee.family_payment.issued`` (receipt numbers, mode, total)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT)
    _check_parts(data)
    first = data.parts[0]
    details = receipts._mode_details(
        ReceiptCreate(
            account_id=first.account_id,
            amount=first.amount,
            mode=data.mode,
            mode_details=data.mode_details,
        )
    )
    # Lock in id order (two clerks taking overlapping families never deadlock).
    locked = {
        account_id: ledger.load_account(session, account_id, lock=True)
        for account_id in sorted(p.account_id for p in data.parts)
    }
    accounts = [locked[p.account_id] for p in data.parts]
    for account in accounts:
        if account.status != "open":
            raise Conflict("This fee account is closed.", code="account_closed")
    seen = students.summaries(session, ctx, {a.student_id for a in accounts})
    if any(a.student_id not in seen for a in accounts):
        raise NotFound("Fee account not found")
    anchor = accounts[0].student_id
    family = {anchor, *_siblings(session, anchor)}
    for i, account in enumerate(accounts):
        if account.student_id not in family:
            raise ValidationFailed(
                [c.error(f"parts.{i}.account_id", "not_in_family")],
                detail="Every account must belong to the same family (children who share a "
                "guardian on their student records).",
            )
    today = c.today(session)
    c.ensure_day_open(session, today)
    planned = []
    for part, account in zip(data.parts, accounts, strict=True):
        dues = ledger.dues_for(session, account, today)
        split = receipts._allocate(dues, part.amount, part.allocations)
        planned.append((part, account, dues, split))
    collector = identity.member_display_names(session, [ctx.membership_id]).get(ctx.membership_id)
    rows = []
    for part, account, dues, split in planned:
        row = receipts.write_receipt(
            session,
            account=account,
            amount=part.amount,
            mode=data.mode,
            details=details,
            utr=None,
            parts=split,
            dues=dues,
            receipt_date=today,
            student=seen[account.student_id],
            collected_by=ctx.membership_id,
            collector_name=collector,
            actor_user_id=ctx.user_id,
        )
        links.close_overlapping(
            session, account.id, {p[0] for p in split}, cancelled_by=ctx.membership_id
        )
        rows.append(row)
    amount = total(p.amount for p in data.parts)
    c.record(
        session,
        action="fee.family_payment.issued",
        resource_type="fee_receipt",
        resource_id=rows[0].id,
        summary={
            "receipts": len(rows),
            "receipt_numbers": [r.receipt_number for r in rows],
            "receipt_ids": [r.id for r in rows],
            "mode": data.mode,
            "amount": c.money_text(amount),
        },
    )
    log.info("fee.family_payment.issued", resource_type="fee_receipt", receipts=len(rows))
    return FamilyPaymentOut(
        receipts=[receipts.receipt_out(session, r) for r in rows], amount=amount, mode=data.mode
    )


# --- hand-over slip (FR-FEE-107) -----------------------------------------------------------------


def slip_page(session: Session, ctx: UserContext, receipt_ids: Sequence[uuid.UUID]) -> str:
    """One A4 page listing the given receipts (``fee.collect`` or ``finance.read``), read from
    their frozen content; cancelled receipts are marked. 422 ``receipt_ids`` (``missing``,
    ``too_many``), 404 for an unknown receipt. Audit ``fee.family_payment.slip_viewed``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    ids = list(dict.fromkeys(receipt_ids))
    if not ids:
        raise ValidationFailed([c.error("receipt_ids", "missing")])
    if len(ids) > load_config().max_accounts:
        raise ValidationFailed([c.error("receipt_ids", "too_many")])
    rows = [receipts.load_receipt(session, rid) for rid in ids]
    school = rows[0].content.get("school") or {}
    lines: list[tuple[str, str, str, str, str, bool]] = []
    amount = ZERO
    for row in rows:
        student = row.content.get("student") or {}
        cancelled = row.status == "cancelled"
        if not cancelled:
            amount += Decimal(str(row.content.get("amount", "0")))
        lines.append(
            (
                row.receipt_number,
                str(student.get("name", "")),
                str(student.get("class_section", "")),
                str(student.get("admission_no", "")),
                group_indian(Decimal(str(row.content.get("amount", "0")))),
                cancelled,
            )
        )
    page = templates_slip(
        school_name=str(school.get("name", "")),
        receipt_date=rows[0].receipt_date.isoformat(),
        mode=templates.MODE_LABELS.get(rows[0].mode, rows[0].mode),
        collector=str(rows[0].content.get("collected_by_name") or ""),
        lines=lines,
        amount=group_indian(amount),
        amount_in_words=in_words(amount) if amount > 0 else "",
    )
    c.record(
        session,
        action="fee.family_payment.slip_viewed",
        resource_type="fee_receipt",
        resource_id=rows[0].id,
        summary={"receipts": len(rows), "receipt_numbers": [r.receipt_number for r in rows]},
    )
    return page


def templates_slip(
    *,
    school_name: str,
    receipt_date: str,
    mode: str,
    collector: str,
    lines: Sequence[tuple[str, str, str, str, str, bool]],
    amount: str,
    amount_in_words: str,
) -> str:
    """The hand-over slip on A4 (the day report's style, allowed by hash in
    :data:`templates.STYLE_CSP`). Every value is escaped and Aadhaar-masked."""
    e = templates._e
    body_rows = "".join(
        f"<tr{' class="cancelled"' if cancelled else ''}><td>{e(n)}</td><td>{e(s)}</td>"
        f'<td>{e(k)}</td><td>{e(a)}</td><td class="num">{e(m)}</td></tr>'
        for n, s, k, a, m, cancelled in lines
    )
    body = (
        f'<main><p class="school">{e(school_name)}</p>'
        f"<h1>Family payment · {e(templates.format_date(receipt_date))}</h1>"
        f"<p>Paid by: <strong>{e(mode)}</strong>"
        + (f" · Collected by {e(collector)}" if collector else "")
        + "</p>"
        "<table><thead><tr><th>Receipt</th><th>Student</th><th>Class</th>"
        '<th>Admission no.</th><th class="num">Amount (₹)</th></tr></thead>'
        f"<tbody>{body_rows}</tbody>"
        f'<tfoot><tr class="totals"><td colspan="4">Total</td>'
        f'<td class="num">{e(amount)}</td></tr></tfoot></table>'
        + (f"<p><em>{e(amount_in_words)}</em></p>" if amount_in_words else "")
        + "<p>One receipt is issued for each child. Keep each child's receipt.</p>"
        '<div class="signatures"><div>Cashier</div><div>Parent</div></div>'
        "<footer>Generated by SchoolOS · personal financial data: keep it with the receipts"
        "</footer></main>"
    )
    return templates._page(
        f"Family payment {receipt_date}", templates.REPORT_STYLE, body, for_pdf=False
    )


__all__ = [
    "CONFIG_PATH",
    "FamilyConfig",
    "family_counter",
    "issue_family_payment",
    "load_config",
    "slip_page",
]
