"""Database access for UPI settings, bank statements and UPI confirmations. Only ``app.fees``
modules call this module.

Every function runs in the caller's ``tenant_session``: RLS limits each statement to the current
school. Fee permissions are school-wide (FR-FEE-024), so there is no per-object scope filter here;
the service decides who may call what.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import Table, and_, exists, func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, NotFound
from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.fees.models import Receipt
from app.fees.upi_models import (
    UPI_MODELS,
    BankStatementImport,
    BankStatementLine,
    UpiConfirmation,
    UpiSettings,
)

# --- errors ---------------------------------------------------------------------------------------

_CONFLICTS: dict[str, tuple[str, str]] = {
    "upi_settings_one_per_school": ("upi_settings_exist", "Reload the UPI settings and try again."),
    "bank_statement_imports_file_once": (
        "statement_already_imported",
        "This statement file was already imported. Upload a newer statement.",
    ),
    "bank_statement_lines_frozen": (
        "line_decided",
        "Someone already decided this bank line. Reload to see it.",
    ),
    "bank_statement_lines_receipt_once": (
        "receipt_taken",
        "This receipt is already matched with another bank credit.",
    ),
    "upi_confirmations_receipt_once": (
        "receipt_already_confirmed",
        "This receipt is already confirmed from the bank statement.",
    ),
    "upi_confirmations_pair_unique": (
        "receipt_already_confirmed",
        "This receipt is already confirmed from this bank credit.",
    ),
    "upi_confirmations_receipt_state": (
        "receipt_not_confirmable",
        "Only an issued UPI receipt can be confirmed from the bank statement.",
    ),
    "upi_confirmations_amount_differs": (
        "amount_mismatch",
        "The bank credit and the receipt have different amounts, so they are not matched. Check "
        "the receipt; a wrong receipt is cancelled and issued again.",
    ),
    "upi_confirmations_utr_differs": (
        "utr_differs",
        "The bank credit carries a different UPI reference than the receipt, so they are not "
        "matched. Check the receipt; a wrong receipt is cancelled and issued again.",
    ),
    "upi_confirmations_line_ignored": (
        "line_decided",
        "This bank line was marked as not a fee payment.",
    ),
    "upi_confirmations_line_taken": (
        "line_taken",
        "This bank credit already confirms another receipt.",
    ),
}


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Map a PostgreSQL error to a domain error (never echoes values); ``None`` if unexpected."""
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or ""
    if constraint in _CONFLICTS:
        code, message = _CONFLICTS[constraint]
        return Conflict(message, code=code)
    return None


# --- settings -------------------------------------------------------------------------------------


def get_settings(session: Session, *, lock: bool = False) -> UpiSettings | None:
    stmt = select(UpiSettings)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def insert_settings(session: Session, values: Mapping[str, Any]) -> UpiSettings:
    tenant_id = current_tenant_id(session)
    row: UpiSettings = session.scalars(
        pg_insert(UpiSettings).values(tenant_id=tenant_id, **values).returning(UpiSettings)
    ).one()
    return row


def update_settings(session: Session, row_id: uuid.UUID, values: Mapping[str, Any]) -> UpiSettings:
    stmt = (
        update(UpiSettings)
        .where(UpiSettings.id == row_id)
        .values(**values, version=UpiSettings.version + 1)
        .returning(UpiSettings)
        .execution_options(synchronize_session=False)
    )
    row: UpiSettings = session.scalars(stmt).one()
    return row


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


# --- imports --------------------------------------------------------------------------------------


def lock_imports(session: Session) -> None:
    """One statement import per school at a time (lines of overlapping statements are compared
    with what is already stored)."""
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended('fees.statement:' || "
            "core.current_tenant(), 0))"
        )
    )


def import_with_sha(session: Session, sha256: bytes) -> BankStatementImport | None:
    stmt = select(BankStatementImport).where(BankStatementImport.file_sha256 == sha256)
    return session.scalars(stmt).first()


def insert_import(session: Session, values: Mapping[str, Any]) -> BankStatementImport:
    tenant_id = current_tenant_id(session)
    row: BankStatementImport = session.scalars(
        pg_insert(BankStatementImport)
        .values(tenant_id=tenant_id, **values)
        .returning(BankStatementImport)
    ).one()
    return row


def get_import(session: Session, import_id: uuid.UUID) -> BankStatementImport | None:
    return session.get(BankStatementImport, import_id)


def list_imports(session: Session, limit: int) -> list[BankStatementImport]:
    stmt = (
        select(BankStatementImport)
        .order_by(BankStatementImport.imported_at.desc(), BankStatementImport.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


# --- lines ----------------------------------------------------------------------------------------


def insert_line(session: Session, values: Mapping[str, Any]) -> bool:
    """Insert one credit; ``False`` when the same reference, amount and date is already stored
    (overlapping statements)."""
    tenant_id = current_tenant_id(session)
    stmt = (
        pg_insert(BankStatementLine)
        .values(tenant_id=tenant_id, **values)
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "utr_or_ref", "amount", "value_date"],
            index_where=BankStatementLine.utr_or_ref.is_not(None),
        )
        .returning(BankStatementLine.id)
    )
    return session.execute(stmt).first() is not None


def existing_credits(
    session: Session, keys: Sequence[tuple[str | None, Decimal, dt.date]]
) -> set[tuple[str, Decimal, dt.date]]:
    """Which ``(reference, amount, value date)`` credits are already stored."""
    refs = {k[0] for k in keys if k[0]}
    if not refs:
        return set()
    stmt = select(
        BankStatementLine.utr_or_ref, BankStatementLine.amount, BankStatementLine.value_date
    ).where(BankStatementLine.utr_or_ref.in_(sorted(refs)))
    return {(str(r[0]), Decimal(r[1]), r[2]) for r in session.execute(stmt)}


def receipts_in_use(session: Session, ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
    """Receipts already proposed for, or confirmed by, a bank credit."""
    if not ids:
        return set()
    stmt = select(BankStatementLine.receipt_id).where(
        BankStatementLine.receipt_id.in_(list(ids)),
        BankStatementLine.match_status.in_(("proposed", "confirmed")),
    )
    return {r for r in session.scalars(stmt) if r is not None}


def lines_of_import(session: Session, import_id: uuid.UUID) -> list[BankStatementLine]:
    stmt = (
        select(BankStatementLine)
        .where(BankStatementLine.import_id == import_id)
        .order_by(BankStatementLine.line_no)
    )
    return list(session.scalars(stmt))


def get_line(
    session: Session, line_id: uuid.UUID, *, lock: bool = False
) -> BankStatementLine | None:
    stmt = select(BankStatementLine).where(BankStatementLine.id == line_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def lock_lines(session: Session, line_ids: Collection[uuid.UUID]) -> list[BankStatementLine]:
    """The lines, locked in id order (no deadlock between two bulk confirmations)."""
    if not line_ids:
        return []
    stmt = (
        select(BankStatementLine)
        .where(BankStatementLine.id.in_(list(line_ids)))
        .order_by(BankStatementLine.id)
        .with_for_update()
    )
    return list(session.scalars(stmt))


def set_line(session: Session, line_id: uuid.UUID, values: Mapping[str, Any]) -> BankStatementLine:
    stmt = (
        update(BankStatementLine)
        .where(BankStatementLine.id == line_id)
        .values(**values, version=BankStatementLine.version + 1)
        .returning(BankStatementLine)
        .execution_options(synchronize_session=False)
    )
    row: BankStatementLine = session.scalars(stmt).one()
    session.expire_all()
    return row


def unmatched_lines(
    session: Session, *, since: dt.date, limit: int, import_id: uuid.UUID | None = None
) -> list[BankStatementLine]:
    stmt = select(BankStatementLine).where(
        BankStatementLine.match_status == "unmatched", BankStatementLine.value_date >= since
    )
    if import_id is not None:
        stmt = stmt.where(BankStatementLine.import_id == import_id)
    stmt = stmt.order_by(BankStatementLine.value_date, BankStatementLine.id).limit(limit)
    return list(session.scalars(stmt))


def proposal_for_receipt(session: Session, receipt_id: uuid.UUID) -> BankStatementLine | None:
    stmt = select(BankStatementLine).where(
        BankStatementLine.receipt_id == receipt_id,
        BankStatementLine.match_status.in_(("proposed", "confirmed")),
    )
    return session.scalars(stmt).first()


def lines_with_utr(session: Session, utr: str) -> bool:
    stmt = select(
        exists().where(BankStatementLine.ref_kind == "utr", BankStatementLine.utr_or_ref == utr)
    )
    return bool(session.execute(stmt).scalar())


def exception_lines(session: Session, limit: int) -> tuple[list[BankStatementLine], int]:
    """Unmatched credits, and confirmed lines whose receipt was cancelled afterwards."""
    cancelled = (
        select(Receipt.id).where(
            Receipt.id == BankStatementLine.receipt_id, Receipt.status == "cancelled"
        )
    ).exists()
    cond = or_(
        BankStatementLine.match_status == "unmatched",
        and_(BankStatementLine.match_status == "confirmed", cancelled),
    )
    count = int(
        session.execute(select(func.count()).select_from(BankStatementLine).where(cond)).scalar()
        or 0
    )
    stmt = (
        select(BankStatementLine)
        .where(cond)
        .order_by(BankStatementLine.value_date.desc(), BankStatementLine.id)
        .limit(limit)
    )
    return list(session.scalars(stmt)), count


def count_proposed(session: Session) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(BankStatementLine)
            .where(BankStatementLine.match_status == "proposed")
        ).scalar()
        or 0
    )


def stale_narrations(session: Session, before: dt.datetime, limit: int) -> list[uuid.UUID]:
    stmt = (
        select(BankStatementLine.id)
        .where(
            BankStatementLine.narration_ciphertext.is_not(None),
            BankStatementLine.created_at < before,
        )
        .order_by(BankStatementLine.created_at)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def erase_narrations(session: Session, line_ids: Sequence[uuid.UUID]) -> int:
    if not line_ids:
        return 0
    stmt = (
        update(BankStatementLine)
        .where(BankStatementLine.id.in_(list(line_ids)))
        .values(
            narration_ciphertext=None,
            narration_erased_at=func.now(),
            version=BankStatementLine.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    result = session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


def _header_version(column: Any) -> Any:
    """Key version of an AES-GCM blob (bytes 2-3 of the header; app.core.crypto)."""
    return func.get_byte(column, 1) * 256 + func.get_byte(column, 2)


def stale_narration_keys(
    session: Session, target_version: int, limit: int
) -> list[BankStatementLine]:
    if limit <= 0:
        return []
    stmt = (
        select(BankStatementLine)
        .where(
            BankStatementLine.narration_ciphertext.is_not(None),
            _header_version(BankStatementLine.narration_ciphertext) != target_version,
        )
        .order_by(BankStatementLine.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(session.scalars(stmt))


def set_narration(session: Session, line_id: uuid.UUID, blob: bytes) -> None:
    session.execute(
        update(BankStatementLine)
        .where(BankStatementLine.id == line_id)
        .values(narration_ciphertext=blob)
        .execution_options(synchronize_session=False)
    )


# --- receipts (read only; fin.receipts belongs to app.fees.receipts) ------------------------------


def _unconfirmed() -> Any:
    return ~exists().where(UpiConfirmation.receipt_id == Receipt.id)


def receipts_by_id(session: Session, ids: Collection[uuid.UUID]) -> dict[uuid.UUID, Receipt]:
    if not ids:
        return {}
    rows = session.scalars(select(Receipt).where(Receipt.id.in_(list(ids))))
    return {r.id: r for r in rows}


def receipt_for_update(session: Session, receipt_id: uuid.UUID) -> Receipt | None:
    stmt = select(Receipt).where(Receipt.id == receipt_id).with_for_update(read=True)
    return session.scalars(stmt).first()


def receipts_with_utr(session: Session, utrs: Collection[str]) -> list[Receipt]:
    """UPI receipts carrying any of ``utrs`` (issued and cancelled), newest first."""
    if not utrs:
        return []
    stmt = (
        select(Receipt)
        .where(Receipt.mode == "upi_qr", Receipt.utr.in_(list(utrs)))
        .order_by(Receipt.issued_at.desc())
    )
    return list(session.scalars(stmt))


def confirmed_receipt_ids(session: Session, ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    stmt = select(UpiConfirmation.receipt_id).where(UpiConfirmation.receipt_id.in_(list(ids)))
    return set(session.scalars(stmt))


def confirmation_times(
    session: Session, ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, dt.datetime]:
    if not ids:
        return {}
    stmt = select(UpiConfirmation.receipt_id, UpiConfirmation.confirmed_at).where(
        UpiConfirmation.receipt_id.in_(list(ids))
    )
    return {r.receipt_id: r.confirmed_at for r in session.execute(stmt)}


def open_upi_receipts_by_amount(
    session: Session, amount: Decimal, date_from: dt.date, date_to: dt.date
) -> list[Receipt]:
    """Issued UPI receipts of ``amount`` dated in the window, not confirmed and not proposed."""
    taken = exists().where(
        BankStatementLine.receipt_id == Receipt.id,
        BankStatementLine.match_status.in_(("proposed", "confirmed")),
    )
    stmt = (
        select(Receipt)
        .where(
            Receipt.mode == "upi_qr",
            Receipt.status == "issued",
            Receipt.amount == amount,
            Receipt.receipt_date >= date_from,
            Receipt.receipt_date <= date_to,
            _unconfirmed(),
            ~taken,
        )
        .order_by(Receipt.receipt_date, Receipt.id)
        .limit(3)
    )
    return list(session.scalars(stmt))


def unconfirmed_receipts(
    session: Session, *, dated_before: dt.date, limit: int
) -> tuple[list[Receipt], int]:
    """Issued UPI receipts dated on or before ``dated_before`` not confirmed yet (FR-FEE-017)."""
    cond = and_(
        Receipt.mode == "upi_qr",
        Receipt.status == "issued",
        Receipt.receipt_date <= dated_before,
        _unconfirmed(),
    )
    count = int(
        session.execute(select(func.count()).select_from(Receipt).where(cond)).scalar() or 0
    )
    stmt = (
        select(Receipt).where(cond).order_by(Receipt.receipt_date, Receipt.receipt_no).limit(limit)
    )
    return list(session.scalars(stmt)), count


def upi_report(
    session: Session, date_from: dt.date, date_to: dt.date
) -> list[tuple[dt.date, bool, int, Decimal]]:
    """``(receipt_date, confirmed, count, total)`` of issued UPI receipts in the range."""
    confirmed = exists().where(UpiConfirmation.receipt_id == Receipt.id)
    stmt = (
        select(
            Receipt.receipt_date,
            confirmed.label("confirmed"),
            func.count(),
            func.coalesce(func.sum(Receipt.amount), literal(Decimal("0.00"))),
        )
        .where(
            Receipt.mode == "upi_qr",
            Receipt.status == "issued",
            Receipt.receipt_date >= date_from,
            Receipt.receipt_date <= date_to,
        )
        .group_by(Receipt.receipt_date, confirmed)
        .order_by(Receipt.receipt_date)
    )
    return [(r[0], bool(r[1]), int(r[2]), Decimal(r[3])) for r in session.execute(stmt)]


# --- confirmations --------------------------------------------------------------------------------


def insert_confirmation(session: Session, values: Mapping[str, Any]) -> UpiConfirmation:
    tenant_id = current_tenant_id(session)
    row: UpiConfirmation = session.scalars(
        pg_insert(UpiConfirmation).values(tenant_id=tenant_id, **values).returning(UpiConfirmation)
    ).one()
    return row


def confirmation_of_receipt(session: Session, receipt_id: uuid.UUID) -> UpiConfirmation | None:
    stmt = select(UpiConfirmation).where(UpiConfirmation.receipt_id == receipt_id)
    return session.scalars(stmt).first()


# --- export ---------------------------------------------------------------------------------------


def export_tables(session: Session) -> list[RecordTable]:
    """The UPI and statement tables for the full data export (FR-FEE-027): narrations
    (ciphertext) and file hashes are left out."""
    out: list[RecordTable] = []
    for model in UPI_MODELS:
        table = model.__table__
        if not isinstance(table, Table):  # pragma: no cover - declarative tables
            raise TypeError(model.__name__)
        exclude: tuple[str, ...] = ()
        if model is BankStatementLine:
            exclude = ("narration_ciphertext",)
        if model is BankStatementImport:
            exclude = ("file_sha256",)
        out.append(
            dump_table(
                session,
                table,
                name=f"fees_{table.name}",
                order_by=("created_at", "id"),
                exclude=exclude,
            )
        )
    return out


def not_found(what: str = "Not found") -> NotFound:
    return NotFound(what)


__all__ = ["translate_db_error"]
