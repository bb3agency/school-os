"""``fin`` tables of UPI settings and bank statements (migration 0060_fees_upi, docs/05 §15.1).

Describes the tables for typed queries only; DDL (RLS, CHECKs, the frozen-line, append-only and
confirmation-check triggers, column grants) lives in the migration. Private to ``app.fees``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import Date, Integer, LargeBinary, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base
from app.fees.models import MONEY, _args


class UpiSettings(Base):
    __tablename__ = "upi_settings"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    payee_name: Mapped[str] = mapped_column(Text)
    vpa: Mapped[str] = mapped_column(Text)
    confirm_within_days: Mapped[int] = mapped_column(Integer, server_default=sql_text("7"))
    updated_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class BankStatementImport(Base):
    __tablename__ = "bank_statement_imports"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    preset_key: Mapped[str] = mapped_column(Text)
    preset_version: Mapped[int] = mapped_column(Integer)
    file_kind: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    period_from: Mapped[dt.date | None] = mapped_column(Date)
    period_to: Mapped[dt.date | None] = mapped_column(Date)
    line_count: Mapped[int] = mapped_column(Integer)
    credit_count: Mapped[int] = mapped_column(Integer)
    duplicate_count: Mapped[int] = mapped_column(Integer)
    proposed_count: Mapped[int] = mapped_column(Integer)
    imported_by: Mapped[uuid.UUID]
    imported_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    file_deleted_at: Mapped[dt.datetime]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class BankStatementLine(Base):
    __tablename__ = "bank_statement_lines"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    import_id: Mapped[uuid.UUID]
    line_no: Mapped[int] = mapped_column(Integer)
    value_date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    ref_kind: Mapped[str] = mapped_column(Text)
    utr_or_ref: Mapped[str | None] = mapped_column(Text)
    narration_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    narration_erased_at: Mapped[dt.datetime | None]
    match_status: Mapped[str] = mapped_column(Text, server_default=sql_text("'unmatched'"))
    match_kind: Mapped[str | None] = mapped_column(Text)
    receipt_id: Mapped[uuid.UUID | None]
    ignore_reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class UpiConfirmation(Base):
    __tablename__ = "upi_confirmations"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    receipt_id: Mapped[uuid.UUID]
    statement_line_id: Mapped[uuid.UUID]
    match_kind: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[uuid.UUID]
    confirmed_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


UPI_MODELS: tuple[type[Base], ...] = (
    UpiSettings,
    BankStatementImport,
    BankStatementLine,
    UpiConfirmation,
)
