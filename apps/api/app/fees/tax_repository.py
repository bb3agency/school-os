"""Database access of the tuition fee certificate (FM-06; FR-FEE-046..060). Internal to
``app.fees``; every query runs in the caller's ``tenant_session`` (RLS) with bound parameters.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Table, func, insert, select, update
from sqlalchemy.orm import Session

from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.fees.models import (
    FeeHead,
    LedgerEntry,
    Receipt,
    ReceiptAllocation,
    StudentFeeAccount,
)
from app.fees.money import ZERO
from app.fees.tax_models import TaxCertificate


@dataclass(frozen=True, slots=True)
class TuitionLine:
    """Tuition paid by one issued receipt of a student (sum of its tuition allocations)."""

    student_id: uuid.UUID
    receipt_id: uuid.UUID
    receipt_number: str
    receipt_date: dt.date
    amount: Decimal


def tuition_heads(session: Session) -> list[FeeHead]:
    """Heads that count as tuition for tax (FR-FEE-047), in print order."""
    stmt = (
        select(FeeHead)
        .where(FeeHead.tax_tuition.is_(True))
        .order_by(FeeHead.sort_order, FeeHead.code)
    )
    return list(session.scalars(stmt))


def tuition_lines(
    session: Session,
    student_ids: Collection[uuid.UUID],
    head_ids: Collection[uuid.UUID],
    start: dt.date,
    end: dt.date,
) -> dict[uuid.UUID, list[TuitionLine]]:
    """Per student, the issued (not cancelled) receipts dated ``start..end`` with their tuition
    allocations summed, oldest first (FR-FEE-048)."""
    if not student_ids or not head_ids:
        return {}
    paid = func.sum(ReceiptAllocation.amount)
    stmt = (
        select(
            StudentFeeAccount.student_id,
            Receipt.id,
            Receipt.receipt_number,
            Receipt.receipt_date,
            paid,
        )
        .join(
            StudentFeeAccount,
            (StudentFeeAccount.tenant_id == Receipt.tenant_id)
            & (StudentFeeAccount.id == Receipt.account_id),
        )
        .join(
            ReceiptAllocation,
            (ReceiptAllocation.tenant_id == Receipt.tenant_id)
            & (ReceiptAllocation.receipt_id == Receipt.id),
        )
        .where(
            StudentFeeAccount.student_id.in_(list(student_ids)),
            Receipt.status == "issued",
            Receipt.receipt_date >= start,
            Receipt.receipt_date <= end,
            ReceiptAllocation.head_id.in_(list(head_ids)),
        )
        # Grouped by the receipt's primary key: its other columns may be selected (PostgreSQL).
        .group_by(StudentFeeAccount.student_id, Receipt.id)
        .order_by(Receipt.receipt_date, Receipt.financial_year, Receipt.receipt_no)
    )
    out: dict[uuid.UUID, list[TuitionLine]] = defaultdict(list)
    for student_id, receipt_id, number, day, amount in session.execute(stmt):
        out[student_id].append(
            TuitionLine(student_id, receipt_id, number, day, Decimal(amount or ZERO))
        )
    return dict(out)


def tuition_refunds(
    session: Session, receipt_ids: Collection[uuid.UUID], head_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """Refunds posted against the tuition lines of ``receipt_ids`` (``refund`` ledger entries
    carry the receipt and the head of the line they refund, FR-FEE-013)."""
    if not receipt_ids or not head_ids:
        return {}
    stmt = (
        select(LedgerEntry.receipt_id, func.sum(LedgerEntry.amount))
        .where(
            LedgerEntry.entry_kind == "refund",
            LedgerEntry.receipt_id.in_(list(receipt_ids)),
            LedgerEntry.head_id.in_(list(head_ids)),
        )
        .group_by(LedgerEntry.receipt_id)
    )
    return {
        receipt_id: Decimal(amount or ZERO)
        for receipt_id, amount in session.execute(stmt)
        if receipt_id is not None
    }


def get_certificate(
    session: Session, certificate_id: uuid.UUID, *, lock: bool = False
) -> TaxCertificate | None:
    stmt = select(TaxCertificate).where(TaxCertificate.id == certificate_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def issued_certificates(
    session: Session, student_ids: Collection[uuid.UUID], financial_year: str, *, lock: bool
) -> dict[uuid.UUID, TaxCertificate]:
    """The issued certificate of each student for the year (at most one each, FR-FEE-052)."""
    if not student_ids:
        return {}
    stmt = select(TaxCertificate).where(
        TaxCertificate.student_id.in_(list(student_ids)),
        TaxCertificate.financial_year == financial_year,
        TaxCertificate.status == "issued",
    )
    if lock:
        stmt = stmt.with_for_update()
    rows = session.scalars(stmt, execution_options={"populate_existing": True})
    return {r.student_id: r for r in rows}


def insert_certificate(session: Session, **values: Any) -> TaxCertificate:
    return session.scalars(insert(TaxCertificate).values(**values).returning(TaxCertificate)).one()


def update_certificate(
    session: Session, certificate_id: uuid.UUID, values: Mapping[str, Any]
) -> TaxCertificate:
    stmt = (
        update(TaxCertificate)
        .where(TaxCertificate.id == certificate_id)
        .values(**values, version=TaxCertificate.version + 1)
        .returning(TaxCertificate)
        .execution_options(synchronize_session=False)
    )
    row: TaxCertificate = session.scalars(stmt, execution_options={"populate_existing": True}).one()
    return row


def list_certificates(
    session: Session,
    *,
    financial_year: str | None,
    student_ids: Collection[uuid.UUID] | None,
    status: str | None,
    after: tuple[dt.datetime, uuid.UUID] | None,
    limit: int,
) -> list[TaxCertificate]:
    """Newest first (``issued_at``, ``id``); filters are codes and ids only."""
    stmt = select(TaxCertificate)
    if financial_year is not None:
        stmt = stmt.where(TaxCertificate.financial_year == financial_year)
    if student_ids is not None:
        if not student_ids:
            return []
        stmt = stmt.where(TaxCertificate.student_id.in_(list(student_ids)))
    if status is not None:
        stmt = stmt.where(TaxCertificate.status == status)
    if after is not None:
        at, last = after
        stmt = stmt.where(
            (TaxCertificate.issued_at < at)
            | ((TaxCertificate.issued_at == at) & (TaxCertificate.id < last))
        )
    stmt = stmt.order_by(TaxCertificate.issued_at.desc(), TaxCertificate.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def export_tables(session: Session) -> list[RecordTable]:
    """The certificates of the current school for its full data export (FR-FEE-027)."""
    table = TaxCertificate.__table__
    if not isinstance(table, Table):  # pragma: no cover - declarative table
        raise TypeError("TaxCertificate")
    return [dump_table(session, table, name="fees_tax_certificates", order_by=("created_at", "id"))]


__all__ = [
    "TuitionLine",
    "export_tables",
    "get_certificate",
    "insert_certificate",
    "issued_certificates",
    "list_certificates",
    "tuition_heads",
    "tuition_lines",
    "tuition_refunds",
    "update_certificate",
]
