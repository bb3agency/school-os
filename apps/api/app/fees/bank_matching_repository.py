"""Database access for cheque and bank transfer matching and cheque status events (FM-08;
FR-FEE-126..150). Only ``app.fees`` modules call this module.

Every function runs in the caller's ``tenant_session``: RLS limits each statement to the current
school. Fee permissions are school-wide (FR-FEE-024), so there is no per-object scope filter here.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import Table, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.fees.cheque_models import CHEQUE_MODELS, ChequeStatusEvent
from app.fees.models import Receipt
from app.fees.upi_models import BankStatementLine, UpiConfirmation
from app.fees.upi_repository import current_tenant_id

CHEQUE_NUMBER = Receipt.mode_details["cheque_number"].astext
TRANSFER_REF = Receipt.mode_details["reference"].astext


def _open() -> list[Any]:
    """Issued, not confirmed from the bank, not proposed for or confirmed by any credit."""
    taken = exists().where(
        BankStatementLine.receipt_id == Receipt.id,
        BankStatementLine.match_status.in_(("proposed", "confirmed")),
    )
    confirmed = exists().where(UpiConfirmation.receipt_id == Receipt.id)
    return [Receipt.status == "issued", ~taken, ~confirmed]


def open_cheque_receipts(session: Session, numbers: Collection[str]) -> list[Receipt]:
    """Open cheque receipts carrying any of ``numbers`` that were not returned."""
    if not numbers:
        return []
    returned = exists().where(
        ChequeStatusEvent.receipt_id == Receipt.id, ChequeStatusEvent.status == "returned"
    )
    stmt = (
        select(Receipt)
        .where(Receipt.mode == "cheque", CHEQUE_NUMBER.in_(list(numbers)), ~returned, *_open())
        .order_by(Receipt.receipt_date, Receipt.id)
    )
    return list(session.scalars(stmt))


def open_transfer_receipts(session: Session, refs: Collection[str]) -> list[Receipt]:
    """Open bank transfer receipts whose reference (stored upper case) is one of ``refs``."""
    if not refs:
        return []
    stmt = (
        select(Receipt)
        .where(Receipt.mode == "bank_transfer", TRANSFER_REF.in_(list(refs)), *_open())
        .order_by(Receipt.receipt_date, Receipt.id)
    )
    return list(session.scalars(stmt))


# --- cheque status events -------------------------------------------------------------------------


def events_of(
    session: Session, receipt_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[ChequeStatusEvent]]:
    if not receipt_ids:
        return {}
    stmt = (
        select(ChequeStatusEvent)
        .where(ChequeStatusEvent.receipt_id.in_(list(receipt_ids)))
        .order_by(ChequeStatusEvent.recorded_at, ChequeStatusEvent.id)
    )
    out: dict[uuid.UUID, list[ChequeStatusEvent]] = {}
    for row in session.scalars(stmt):
        out.setdefault(row.receipt_id, []).append(row)
    return out


def returned_receipt_ids(session: Session, receipt_ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
    if not receipt_ids:
        return set()
    stmt = select(ChequeStatusEvent.receipt_id).where(
        ChequeStatusEvent.receipt_id.in_(list(receipt_ids)), ChequeStatusEvent.status == "returned"
    )
    return set(session.scalars(stmt))


def cheque_status_of(session: Session, receipt: Receipt) -> str | None:
    """A cheque receipt's latest status (``received`` before any event); ``None`` otherwise."""
    if receipt.mode != "cheque":
        return None
    have = {e.status for e in events_of(session, [receipt.id]).get(receipt.id, [])}
    for status in ("returned", "cleared", "deposited"):
        if status in have:
            return status
    return "received"


def insert_event(session: Session, values: Mapping[str, Any]) -> ChequeStatusEvent:
    tenant_id = current_tenant_id(session)
    row: ChequeStatusEvent = session.scalars(
        pg_insert(ChequeStatusEvent)
        .values(tenant_id=tenant_id, **values)
        .returning(ChequeStatusEvent)
    ).one()
    return row


def cheque_receipts(
    session: Session,
    *,
    date_from: dt.date,
    date_to: dt.date,
    limit: int,
) -> list[Receipt]:
    """Cheque receipts dated in the range (issued and cancelled), oldest first."""
    stmt = (
        select(Receipt)
        .where(
            Receipt.mode == "cheque",
            Receipt.receipt_date >= date_from,
            Receipt.receipt_date <= date_to,
        )
        .order_by(Receipt.receipt_date, Receipt.receipt_no)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def deposited_on(session: Session, day: dt.date, limit: int) -> list[Receipt]:
    """Cheque receipts marked deposited on ``day`` (the deposit slip)."""
    stmt = (
        select(Receipt)
        .join(
            ChequeStatusEvent,
            (ChequeStatusEvent.receipt_id == Receipt.id)
            & (ChequeStatusEvent.status == "deposited"),
        )
        .where(ChequeStatusEvent.event_date == day)
        .order_by(Receipt.receipt_date, Receipt.receipt_no)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def receipts_for_update(session: Session, ids: Collection[uuid.UUID]) -> list[Receipt]:
    if not ids:
        return []
    stmt = (
        select(Receipt)
        .where(Receipt.id.in_(list(ids)))
        .order_by(Receipt.id)
        .with_for_update(read=True)
    )
    return list(session.scalars(stmt))


def export_tables(session: Session) -> list[RecordTable]:
    """``fin.cheque_status_events`` for the full data export (FR-FEE-027)."""
    out: list[RecordTable] = []
    for model in CHEQUE_MODELS:
        table = model.__table__
        if not isinstance(table, Table):  # pragma: no cover - declarative tables
            raise TypeError(model.__name__)
        out.append(
            dump_table(session, table, name=f"fees_{table.name}", order_by=("created_at", "id"))
        )
    return out


__all__ = [
    "cheque_receipts",
    "cheque_status_of",
    "deposited_on",
    "events_of",
    "export_tables",
    "insert_event",
    "open_cheque_receipts",
    "open_transfer_receipts",
    "receipts_for_update",
    "returned_receipt_ids",
]
