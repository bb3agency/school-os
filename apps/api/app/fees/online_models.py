"""``fin`` tables of online payments (migration 0059_payments_online, docs/05 §15.2).

Describes the tables for typed queries only; DDL (RLS, CHECKs, the intent and reconciliation
guards, column grants) lives in the migration. Internal to ``app.fees`` (import-linter
``fees-internals``): other modules use ``app.fees.service``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import Date, Integer, LargeBinary, Numeric, Text, UniqueConstraint, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "fin"
MONEY = Numeric(14, 2)


def _args() -> tuple[object, ...]:
    return (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})


class PaymentConnection(Base):
    __tablename__ = "payment_connections"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    provider: Mapped[str] = mapped_column(Text, server_default=sql_text("'razorpay'"))
    mode: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    provider_account_id: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str | None] = mapped_column(Text)
    key_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    refresh_token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_expires_at: Mapped[dt.datetime | None]
    refresh_expires_at: Mapped[dt.datetime | None]
    webhook_id: Mapped[str | None] = mapped_column(Text)
    webhook_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    previous_webhook_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    previous_until: Mapped[dt.datetime | None]
    route_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    route_handle_id: Mapped[uuid.UUID | None]
    connected_by: Mapped[uuid.UUID | None]
    connected_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    secret_rotated_at: Mapped[dt.datetime | None]
    last_checked_at: Mapped[dt.datetime | None]
    last_error_code: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[dt.datetime | None]
    disconnected_by: Mapped[uuid.UUID | None]
    disconnected_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class PaymentIntent(Base):
    __tablename__ = "payment_intents"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    connection_id: Mapped[uuid.UUID]
    intent_code: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    instalments: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'created'"))
    provider_link_id: Mapped[str] = mapped_column(Text)
    short_url: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[dt.datetime]
    created_via: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None]
    public_handle_id: Mapped[uuid.UUID | None]
    provider_payment_id: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[dt.datetime | None]
    cancelled_by: Mapped[uuid.UUID | None]
    cancelled_at: Mapped[dt.datetime | None]
    closed_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class ProviderEvent(Base):
    __tablename__ = "provider_events"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    provider: Mapped[str] = mapped_column(Text, server_default=sql_text("'razorpay'"))
    connection_id: Mapped[uuid.UUID]
    event_id: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    event_at: Mapped[dt.datetime | None]
    received_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    payload_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'received'"))
    outcome: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    next_attempt_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    error_code: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[dt.datetime | None]


class ProviderPayment(Base):
    __tablename__ = "provider_payments"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    provider: Mapped[str] = mapped_column(Text, server_default=sql_text("'razorpay'"))
    connection_id: Mapped[uuid.UUID]
    provider_payment_id: Mapped[str] = mapped_column(Text)
    provider_order_id: Mapped[str | None] = mapped_column(Text)
    intent_id: Mapped[uuid.UUID | None]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    amount_refunded: Mapped[Decimal] = mapped_column(MONEY, server_default=sql_text("0"))
    provider_fee: Mapped[Decimal | None] = mapped_column(MONEY)
    provider_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    paid_at: Mapped[dt.datetime | None]
    settlement_id: Mapped[str | None] = mapped_column(Text)
    settled_at: Mapped[dt.datetime | None]
    receipt_id: Mapped[uuid.UUID | None]
    first_seen_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    last_fetched_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    provider: Mapped[str] = mapped_column(Text, server_default=sql_text("'razorpay'"))
    run_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'running'"))
    payments_seen: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    exceptions_opened: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[dt.datetime | None]


class ReconciliationItem(Base):
    __tablename__ = "reconciliation_items"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    run_id: Mapped[uuid.UUID | None]
    kind: Mapped[str] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text)
    provider_payment_id: Mapped[str | None] = mapped_column(Text)
    provider_refund_id: Mapped[str | None] = mapped_column(Text)
    intent_id: Mapped[uuid.UUID | None]
    receipt_id: Mapped[uuid.UUID | None]
    refund_id: Mapped[uuid.UUID | None]
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    expected_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'open'"))
    note: Mapped[str | None] = mapped_column(Text)
    explained_by: Mapped[uuid.UUID | None]
    explained_at: Mapped[dt.datetime | None]
    resolved_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


ALL_MODELS: tuple[type[Base], ...] = (
    PaymentConnection,
    PaymentIntent,
    ProviderEvent,
    ProviderPayment,
    ReconciliationRun,
    ReconciliationItem,
)
