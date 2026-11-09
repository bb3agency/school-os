"""What the Tally voucher export reads from the fee ledger (FM-03; FR-TALLY-011..030; internal,
re-exported by ``app.fees.service``).

System reads for the caller's school (the caller, ``app.tally``, checks ``fee.report`` /
``finance.read`` school-wide and that fees are on). Nothing here writes. Amounts are ``Decimal``
rupees; ids and display fields only, never logged.

- **Cash vouchers** of a period: receipts by ``receipt_date``, approved cancellations by
  ``cancelled_on`` and processed refunds by ``voucher_date``, each with the student's id, name and
  class as frozen on the receipt (the name becomes a party name or narration in the file).
- **Demand** of a period (optional in the export): the ledger entries that change what a student
  owes other than by money (``charge``, ``late_fee``, ``concession``, ``adjustment``).
- **Open days**: days of the period that have cash vouchers but no day close (FR-FEE-019); the
  export refuses them so a file never covers a day that can still change.
- **Balances as of a date**: the sum of every ledger entry of each student up to and including
  that date, all years (the ledger-vs-Tally check compares it with Tally's party balance).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.fees.models import (
    DayClose,
    FeeHead,
    LedgerEntry,
    Receipt,
    ReceiptCancellation,
    Refund,
    StudentFeeAccount,
)
from app.fees.money import ZERO

MODES: tuple[str, ...] = ("cash", "cheque", "bank_transfer", "upi_qr", "razorpay")
"""Receipt modes in display order (the same list as the daily report)."""
DEMAND_KINDS: tuple[str, ...] = ("charge", "late_fee", "concession", "adjustment")


@dataclass(frozen=True, slots=True)
class FeedHead:
    id: uuid.UUID
    code: str
    name: str
    active: bool


@dataclass(frozen=True, slots=True)
class FeedStudent:
    student_id: uuid.UUID
    name: str
    class_section: str


@dataclass(frozen=True, slots=True)
class FeedCash:
    """One cash voucher: ``kind`` ``receipt`` (money in), ``cancellation`` or ``refund`` (money
    back out, or never received)."""

    kind: str
    id: uuid.UUID
    number: str
    receipt_number: str
    on: dt.date
    amount: Decimal
    mode: str
    student: FeedStudent


@dataclass(frozen=True, slots=True)
class FeedDemand:
    """One ledger entry that changes what is owed (positive: owed more)."""

    id: uuid.UUID
    kind: str
    on: dt.date
    amount: Decimal
    head_id: uuid.UUID | None
    account_id: uuid.UUID
    student_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class TallyFeed:
    cash: tuple[FeedCash, ...]
    demand: tuple[FeedDemand, ...]
    open_days: tuple[dt.date, ...]


def heads(session: Session) -> list[FeedHead]:
    """Every fee head of the school (inactive ones too: old receipts may use them)."""
    rows = session.execute(
        select(FeeHead.id, FeeHead.code, FeeHead.name, FeeHead.active).order_by(
            FeeHead.sort_order, FeeHead.code
        )
    )
    return [FeedHead(id=r.id, code=r.code, name=r.name, active=r.active) for r in rows]


def _student(receipt: Receipt, student_id: uuid.UUID) -> FeedStudent:
    frozen = receipt.content.get("student") or {}
    return FeedStudent(
        student_id=student_id,
        name=str(frozen.get("name", "")),
        class_section=str(frozen.get("class_section", "")),
    )


def _receipts(session: Session, ids: Collection[uuid.UUID]) -> dict[uuid.UUID, Receipt]:
    if not ids:
        return {}
    return {r.id: r for r in session.scalars(select(Receipt).where(Receipt.id.in_(list(ids))))}


def _students_of_accounts(
    session: Session, account_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    if not account_ids:
        return {}
    rows = session.execute(
        select(StudentFeeAccount.id, StudentFeeAccount.student_id).where(
            StudentFeeAccount.id.in_(list(account_ids))
        )
    )
    return {r.id: r.student_id for r in rows}


def tally_feed(session: Session, start: dt.date, end: dt.date, *, demand: bool) -> TallyFeed:
    """The cash vouchers (and, when ``demand``, the demand entries) dated ``start``..``end``
    inclusive, oldest first, and the open days among them."""
    receipts = list(
        session.scalars(
            select(Receipt)
            .where(Receipt.receipt_date >= start, Receipt.receipt_date <= end)
            .order_by(Receipt.receipt_date, Receipt.receipt_no)
        )
    )
    cancellations = list(
        session.scalars(
            select(ReceiptCancellation)
            .where(
                ReceiptCancellation.status == "approved",
                ReceiptCancellation.cancelled_on >= start,
                ReceiptCancellation.cancelled_on <= end,
            )
            .order_by(ReceiptCancellation.cancelled_on, ReceiptCancellation.decided_at)
        )
    )
    refunds = list(
        session.scalars(
            select(Refund)
            .where(
                Refund.status == "processed",
                Refund.voucher_date >= start,
                Refund.voucher_date <= end,
            )
            .order_by(Refund.voucher_date, Refund.voucher_no)
        )
    )
    by_id = {r.id: r for r in receipts}
    by_id.update(
        _receipts(
            session,
            ({x.receipt_id for x in cancellations} | {f.receipt_id for f in refunds}) - set(by_id),
        )
    )
    student_of = _students_of_accounts(session, {r.account_id for r in by_id.values()})

    def student(receipt: Receipt) -> FeedStudent:
        return _student(receipt, student_of[receipt.account_id])

    cash: list[FeedCash] = [
        FeedCash(
            kind="receipt",
            id=r.id,
            number=r.receipt_number,
            receipt_number=r.receipt_number,
            on=r.receipt_date,
            amount=r.amount,
            mode=r.mode,
            student=student(r),
        )
        for r in receipts
    ]
    for x in cancellations:
        r = by_id[x.receipt_id]
        if x.cancelled_on is None:  # approved cancellations always carry their date
            continue
        cash.append(
            FeedCash(
                kind="cancellation",
                id=x.id,
                number=f"{r.receipt_number}-C",
                receipt_number=r.receipt_number,
                on=x.cancelled_on,
                amount=r.amount,
                mode=r.mode,
                student=student(r),
            )
        )
    for f in refunds:
        r = by_id[f.receipt_id]
        if f.voucher_date is None:  # processed refunds always carry their voucher
            continue
        cash.append(
            FeedCash(
                kind="refund",
                id=f.id,
                number=f.voucher_number or f"{r.receipt_number}-R",
                receipt_number=r.receipt_number,
                on=f.voucher_date,
                amount=f.amount,
                mode=f.mode,
                student=student(r),
            )
        )
    order = {"receipt": 0, "cancellation": 1, "refund": 2}
    cash.sort(key=lambda v: (v.on, order[v.kind], v.number))

    entries: list[FeedDemand] = []
    if demand:
        rows = session.execute(
            select(
                LedgerEntry.id,
                LedgerEntry.entry_kind,
                LedgerEntry.entry_date,
                LedgerEntry.amount,
                LedgerEntry.head_id,
                LedgerEntry.account_id,
                StudentFeeAccount.student_id,
            )
            .join(StudentFeeAccount, StudentFeeAccount.id == LedgerEntry.account_id)
            .where(
                LedgerEntry.entry_kind.in_(DEMAND_KINDS),
                LedgerEntry.entry_date >= start,
                LedgerEntry.entry_date <= end,
            )
            .order_by(LedgerEntry.entry_date, LedgerEntry.account_id, LedgerEntry.id)
        )
        entries = [
            FeedDemand(
                id=r.id,
                kind=r.entry_kind,
                on=r.entry_date,
                amount=r.amount,
                head_id=r.head_id,
                account_id=r.account_id,
                student_id=r.student_id,
            )
            for r in rows
        ]

    days = {v.on for v in cash}
    closed = (
        set(session.scalars(select(DayClose.close_date).where(DayClose.close_date.in_(list(days)))))
        if days
        else set()
    )
    return TallyFeed(
        cash=tuple(cash), demand=tuple(entries), open_days=tuple(sorted(days - closed))
    )


def balances_as_of(
    session: Session, as_of: dt.date, student_ids: Collection[uuid.UUID] | None = None
) -> dict[uuid.UUID, Decimal]:
    """What each student owes on ``as_of`` (end of day): every ledger entry of every year up to
    that date. Students with no entries are left out; ``student_ids`` None means all."""
    stmt = (
        select(StudentFeeAccount.student_id, func.sum(LedgerEntry.amount))
        .join(StudentFeeAccount, StudentFeeAccount.id == LedgerEntry.account_id)
        .where(LedgerEntry.entry_date <= as_of)
        .group_by(StudentFeeAccount.student_id)
    )
    if student_ids is not None:
        if not student_ids:
            return {}
        stmt = stmt.where(StudentFeeAccount.student_id.in_(list(student_ids)))
    return {sid: Decimal(total or ZERO) for sid, total in session.execute(stmt)}


__all__ = [
    "DEMAND_KINDS",
    "MODES",
    "FeedCash",
    "FeedDemand",
    "FeedHead",
    "FeedStudent",
    "TallyFeed",
    "balances_as_of",
    "heads",
    "tally_feed",
]
