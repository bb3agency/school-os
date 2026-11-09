"""Reads of the accountant and management reports (FM-04; FR-FEE-061..075).

SQL aggregates on ``fin`` in the caller's ``tenant_session`` (RLS), bound parameters only.
Date ranges use the existing indexes: ``receipts_date_idx`` (receipt date),
``receipt_cancellations_day_idx`` (cancelled on), ``refunds_voucher_day_idx`` (voucher date) and
``ledger_entries_account_idx`` / ``ledger_entries_receipt_idx``. Nothing here writes.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.fees.models import (
    Adjustment,
    Concession,
    LedgerEntry,
    Receipt,
    ReceiptAllocation,
    ReceiptCancellation,
    ReceiptCounter,
    Refund,
    StudentFeeAccount,
)


@dataclass(frozen=True, slots=True)
class ReceiptLine:
    id: uuid.UUID
    account_id: uuid.UUID
    receipt_number: str
    receipt_no: int
    receipt_date: dt.date
    amount: Decimal
    mode: str
    status: str
    collected_by: uuid.UUID | None
    student: dict[str, Any]


def receipts_between(session: Session, first: dt.date, last: dt.date) -> list[ReceiptLine]:
    """Receipts numbered on ``first``..``last`` (any status), in date and number order. The
    frozen content gives only the student block (name and class at issue)."""
    stmt = (
        select(
            Receipt.id,
            Receipt.account_id,
            Receipt.receipt_number,
            Receipt.receipt_no,
            Receipt.receipt_date,
            Receipt.amount,
            Receipt.mode,
            Receipt.status,
            Receipt.collected_by,
            Receipt.content["student"],
        )
        .where(Receipt.receipt_date >= first, Receipt.receipt_date <= last)
        .order_by(Receipt.receipt_date, Receipt.receipt_no)
    )
    return [
        ReceiptLine(
            r[0], r[1], r[2], int(r[3]), r[4], Decimal(r[5]), r[6], r[7], r[8], dict(r[9] or {})
        )
        for r in session.execute(stmt)
    ]


def cancellations_between(
    session: Session, first: dt.date, last: dt.date
) -> list[ReceiptCancellation]:
    stmt = (
        select(ReceiptCancellation)
        .where(
            ReceiptCancellation.status == "approved",
            ReceiptCancellation.cancelled_on >= first,
            ReceiptCancellation.cancelled_on <= last,
        )
        .order_by(ReceiptCancellation.cancelled_on, ReceiptCancellation.decided_at)
    )
    return list(session.scalars(stmt))


def refunds_between(session: Session, first: dt.date, last: dt.date) -> list[Refund]:
    stmt = (
        select(Refund)
        .where(
            Refund.status == "processed",
            Refund.voucher_date >= first,
            Refund.voucher_date <= last,
        )
        .order_by(Refund.voucher_date, Refund.voucher_no)
    )
    return list(session.scalars(stmt))


def allocated_by_head(
    session: Session, *, first: dt.date, last: dt.date
) -> dict[uuid.UUID | None, Decimal]:
    """Allocations of the receipts numbered in the period, per head (``None``: late fees)."""
    stmt = (
        select(ReceiptAllocation.head_id, func.sum(ReceiptAllocation.amount))
        .join(
            Receipt,
            and_(
                Receipt.tenant_id == ReceiptAllocation.tenant_id,
                Receipt.id == ReceiptAllocation.receipt_id,
            ),
        )
        .where(Receipt.receipt_date >= first, Receipt.receipt_date <= last)
        .group_by(ReceiptAllocation.head_id)
    )
    return {r[0]: Decimal(r[1]) for r in session.execute(stmt)}


def allocations_of_receipts_by_head(
    session: Session, receipt_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID | None, Decimal]:
    if not receipt_ids:
        return {}
    stmt = (
        select(ReceiptAllocation.head_id, func.sum(ReceiptAllocation.amount))
        .where(ReceiptAllocation.receipt_id.in_(list(receipt_ids)))
        .group_by(ReceiptAllocation.head_id)
    )
    return {r[0]: Decimal(r[1]) for r in session.execute(stmt)}


def refunded_by_head(
    session: Session, refunds: Collection[Refund]
) -> dict[uuid.UUID | None, Decimal]:
    """Refund ledger entries of ``refunds`` per head (positive amounts)."""
    if not refunds:
        return {}
    stmt = (
        select(LedgerEntry.head_id, func.sum(LedgerEntry.amount))
        .where(
            LedgerEntry.receipt_id.in_(list({r.receipt_id for r in refunds})),
            LedgerEntry.refund_id.in_([r.id for r in refunds]),
            LedgerEntry.entry_kind == "refund",
        )
        .group_by(LedgerEntry.head_id)
    )
    return {r[0]: Decimal(r[1]) for r in session.execute(stmt)}


# --- demand, collection, balance -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountSums:
    """Ledger sums of one account by kind (signed as stored: credits negative); ``write_off``
    is the part of ``adjustment`` posted by approved write-offs."""

    account_id: uuid.UUID
    student_id: uuid.UUID
    plan_id: uuid.UUID
    academic_year_id: uuid.UUID
    status: str
    sums: dict[str, Decimal]
    write_off: Decimal


def account_sums(
    session: Session,
    year_ids: Collection[uuid.UUID],
    *,
    open_only_for: Collection[uuid.UUID] = (),
) -> list[AccountSums]:
    """Every account of ``year_ids`` (only the open ones for the years in ``open_only_for``)
    with its ledger sums by entry kind, write-offs apart."""
    if not year_ids:
        return []
    open_years = list(open_only_for)
    accounts = select(StudentFeeAccount).where(
        StudentFeeAccount.academic_year_id.in_(list(year_ids))
    )
    if open_years:
        accounts = accounts.where(
            (StudentFeeAccount.status == "open")
            | StudentFeeAccount.academic_year_id.not_in(open_years)
        )
    rows = list(session.scalars(accounts.order_by(StudentFeeAccount.id)))
    if not rows:
        return []
    wo = case((Adjustment.reason_code == "write_off", LedgerEntry.amount), else_=0)
    stmt = (
        select(
            LedgerEntry.account_id,
            LedgerEntry.entry_kind,
            func.sum(LedgerEntry.amount),
            func.sum(wo),
        )
        .outerjoin(
            Adjustment,
            and_(
                Adjustment.tenant_id == LedgerEntry.tenant_id,
                Adjustment.id == LedgerEntry.adjustment_id,
            ),
        )
        .where(LedgerEntry.account_id.in_([a.id for a in rows]))
        .group_by(LedgerEntry.account_id, LedgerEntry.entry_kind)
    )
    sums: dict[uuid.UUID, dict[str, Decimal]] = {a.id: {} for a in rows}
    write_off: dict[uuid.UUID, Decimal] = {}
    for account_id, kind, amount, written in session.execute(stmt):
        sums[account_id][str(kind)] = Decimal(amount)
        if kind == "adjustment":
            write_off[account_id] = Decimal(written or 0)
    return [
        AccountSums(
            account_id=a.id,
            student_id=a.student_id,
            plan_id=a.plan_id,
            academic_year_id=a.academic_year_id,
            status=a.status,
            sums=sums[a.id],
            write_off=write_off.get(a.id, Decimal("0")),
        )
        for a in rows
    ]


# --- registers -----------------------------------------------------------------------------------


def concessions_decided_between(
    session: Session, start: dt.datetime, end: dt.datetime
) -> list[Concession]:
    """Approved concessions decided in ``[start, end)`` (UTC instants of the IST dates)."""
    stmt = (
        select(Concession)
        .where(
            Concession.status == "approved",
            Concession.decided_at >= start,
            Concession.decided_at < end,
        )
        .order_by(Concession.decided_at, Concession.id)
    )
    return list(session.scalars(stmt))


def adjustments_decided_between(
    session: Session, start: dt.datetime, end: dt.datetime
) -> list[Adjustment]:
    """Approved adjustments decided in ``[start, end)``; plan-change adjustments made by the
    system on activation are approved at creation and appear too."""
    stmt = (
        select(Adjustment)
        .where(
            Adjustment.status == "approved",
            func.coalesce(Adjustment.decided_at, Adjustment.requested_at) >= start,
            func.coalesce(Adjustment.decided_at, Adjustment.requested_at) < end,
        )
        .order_by(func.coalesce(Adjustment.decided_at, Adjustment.requested_at), Adjustment.id)
    )
    return list(session.scalars(stmt))


# --- receipt series ------------------------------------------------------------------------------


def receipt_numbers(session: Session, financial_year: str) -> list[tuple[int, str, str]]:
    """(number, printed number, status) of every receipt of the financial year."""
    stmt = (
        select(Receipt.receipt_no, Receipt.receipt_number, Receipt.status)
        .where(Receipt.financial_year == financial_year)
        .order_by(Receipt.receipt_no)
    )
    return [(int(r[0]), str(r[1]), str(r[2])) for r in session.execute(stmt)]


def voucher_numbers(session: Session, financial_year: str) -> list[tuple[int, str]]:
    stmt = (
        select(Refund.voucher_no, Refund.voucher_number)
        .where(Refund.financial_year == financial_year, Refund.voucher_no.is_not(None))
        .order_by(Refund.voucher_no)
    )
    return [(int(r[0] or 0), str(r[1])) for r in session.execute(stmt)]


def counter_value(session: Session, series: str, financial_year: str) -> int:
    stmt = select(ReceiptCounter.last_no).where(
        ReceiptCounter.series == series, ReceiptCounter.financial_year == financial_year
    )
    found = session.scalars(stmt).first()
    return int(found) if found is not None else 0


__all__ = [
    "AccountSums",
    "ReceiptLine",
    "account_sums",
    "adjustments_decided_between",
    "allocated_by_head",
    "allocations_of_receipts_by_head",
    "cancellations_between",
    "concessions_decided_between",
    "counter_value",
    "receipt_numbers",
    "receipts_between",
    "refunded_by_head",
    "voucher_numbers",
]
