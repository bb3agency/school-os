"""``fin`` tables of the fee ledger and receipts (migration 0058_fees_core, docs/05 §15.1).

Describes the tables for typed queries only; DDL (RLS, CHECKs, the append-only, frozen-receipt
and deferred allocation-sum triggers, column grants) lives in the migration. FKs into other
modules' tables are enforced by the database and not declared here, so this module never imports
another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, Integer, LargeBinary, Numeric, Text, UniqueConstraint, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "fin"
MONEY = Numeric(14, 2)


def _args() -> tuple[Any, ...]:
    return (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})


class FeeSettings(Base):
    __tablename__ = "fee_settings"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    paper_size: Mapped[str] = mapped_column(Text, server_default=sql_text("'A5'"))
    gst_note: Mapped[str] = mapped_column(Text)
    reminder_offsets: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    min_reminder_amount: Mapped[Decimal] = mapped_column(MONEY)
    updated_by: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class FeeHead(Base):
    __tablename__ = "fee_heads"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    gst_exempt: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("true"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, server_default=sql_text("100"))
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class FeePlan(Base):
    __tablename__ = "fee_plans"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    academic_year_id: Mapped[uuid.UUID]
    class_id: Mapped[uuid.UUID]
    category_code: Mapped[str | None] = mapped_column(Text)
    plan_version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'draft'"))
    name: Mapped[str] = mapped_column(Text)
    late_fee_rule: Mapped[dict[str, Any]] = mapped_column(JSONB)
    previous_plan_id: Mapped[uuid.UUID | None]
    created_by: Mapped[uuid.UUID]
    activated_by: Mapped[uuid.UUID | None]
    activated_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class FeePlanLine(Base):
    __tablename__ = "fee_plan_lines"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    plan_id: Mapped[uuid.UUID]
    head_id: Mapped[uuid.UUID]
    annual_amount: Mapped[Decimal] = mapped_column(MONEY)
    sort_order: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class FeePlanInstalment(Base):
    __tablename__ = "fee_plan_instalments"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    plan_id: Mapped[uuid.UUID]
    instalment_no: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)
    due_on: Mapped[dt.date] = mapped_column(Date)
    amounts: Mapped[dict[str, str]] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class StudentFeeAccount(Base):
    __tablename__ = "student_fee_accounts"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    academic_year_id: Mapped[uuid.UUID]
    plan_id: Mapped[uuid.UUID]
    category_code: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'open'"))
    opened_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    entry_kind: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    instalment_no: Mapped[int] = mapped_column(Integer)
    head_id: Mapped[uuid.UUID | None]
    entry_date: Mapped[dt.date] = mapped_column(Date)
    receipt_id: Mapped[uuid.UUID | None]
    cancellation_id: Mapped[uuid.UUID | None]
    concession_id: Mapped[uuid.UUID | None]
    adjustment_id: Mapped[uuid.UUID | None]
    refund_id: Mapped[uuid.UUID | None]
    late_fee_key: Mapped[str | None] = mapped_column(Text)
    posted_by: Mapped[uuid.UUID | None]
    posted_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class Concession(Base):
    __tablename__ = "concessions"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    concession_type: Mapped[str] = mapped_column(Text)
    basis: Mapped[str] = mapped_column(Text)
    value: Mapped[Decimal] = mapped_column(MONEY)
    targets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    evidence_document_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'pending'"))
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    requested_by: Mapped[uuid.UUID]
    requested_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[dt.datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class Adjustment(Base):
    __tablename__ = "adjustments"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    instalment_no: Mapped[int] = mapped_column(Integer)
    head_id: Mapped[uuid.UUID | None]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    reason_code: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    source_plan_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'pending'"))
    requested_by: Mapped[uuid.UUID]
    requested_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[dt.datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class ReceiptCounter(Base):
    __tablename__ = "receipt_counters"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    series: Mapped[str] = mapped_column(Text)
    financial_year: Mapped[str] = mapped_column(Text)
    last_no: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    financial_year: Mapped[str] = mapped_column(Text)
    receipt_no: Mapped[int] = mapped_column(Integer)
    receipt_number: Mapped[str] = mapped_column(Text)
    receipt_date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    mode: Mapped[str] = mapped_column(Text)
    mode_details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    utr: Mapped[str | None] = mapped_column(Text)
    provider_payment_id: Mapped[str | None] = mapped_column(Text)
    payment_intent_id: Mapped[uuid.UUID | None]
    collected_by: Mapped[uuid.UUID | None]
    issued_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'issued'"))
    document_id: Mapped[uuid.UUID | None]
    pdf_status: Mapped[str] = mapped_column(Text, server_default=sql_text("'queued'"))
    pdf_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class ReceiptAllocation(Base):
    __tablename__ = "receipt_allocations"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    receipt_id: Mapped[uuid.UUID]
    instalment_no: Mapped[int] = mapped_column(Integer)
    head_id: Mapped[uuid.UUID | None]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class ReceiptCancellation(Base):
    __tablename__ = "receipt_cancellations"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    receipt_id: Mapped[uuid.UUID]
    reason_code: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'pending'"))
    requested_by: Mapped[uuid.UUID]
    requested_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[dt.datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)
    cancelled_on: Mapped[dt.date | None] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    receipt_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    mode: Mapped[str] = mapped_column(Text)
    mode_details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'pending'"))
    requested_by: Mapped[uuid.UUID]
    requested_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[dt.datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)
    financial_year: Mapped[str | None] = mapped_column(Text)
    voucher_no: Mapped[int | None] = mapped_column(Integer)
    voucher_number: Mapped[str | None] = mapped_column(Text)
    voucher_date: Mapped[dt.date | None] = mapped_column(Date)
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    content_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    provider_refund_id: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class DayClose(Base):
    __tablename__ = "day_closes"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    close_date: Mapped[dt.date] = mapped_column(Date)
    receipt_count: Mapped[int] = mapped_column(Integer)
    receipt_total: Mapped[Decimal] = mapped_column(MONEY)
    totals: Mapped[dict[str, Any]] = mapped_column(JSONB)
    closed_by: Mapped[uuid.UUID]
    closed_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


ALL_MODELS: tuple[type[Base], ...] = (
    FeeSettings,
    FeeHead,
    FeePlan,
    FeePlanLine,
    FeePlanInstalment,
    StudentFeeAccount,
    LedgerEntry,
    Concession,
    Adjustment,
    ReceiptCounter,
    Receipt,
    ReceiptAllocation,
    ReceiptCancellation,
    Refund,
    DayClose,
)
