"""Messaging tables in schema ``msg`` (migration 0057_messaging_core; docs/05 §15.3; ADR-0043).

Models describe tables for typed queries only; DDL (RLS, grants, triggers, composite FKs into
``sis.guardians``, ``sis.students``, ``kb.documents``, ``core.public_handles`` and
``core.memberships``) lives in the migration, so this module never imports another module's
models.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    Integer,
    LargeBinary,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "msg"


class Account(Base):
    """The school's connected WhatsApp Business account and number (one live row per school)."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    provider: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    business_id: Mapped[str | None] = mapped_column(Text)
    waba_id: Mapped[str] = mapped_column(Text)
    phone_number_id: Mapped[str] = mapped_column(Text)
    display_phone: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    pause_reason: Mapped[str | None] = mapped_column(Text)
    quality_rating: Mapped[str | None] = mapped_column(Text)
    messaging_limit: Mapped[str | None] = mapped_column(Text)
    number_status: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    currency_confirmed_at: Mapped[dt.datetime | None]
    token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    pin_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    waba_handle_id: Mapped[uuid.UUID | None]
    connected_by: Mapped[uuid.UUID | None]
    connected_at: Mapped[dt.datetime | None]
    disconnected_by: Mapped[uuid.UUID | None]
    disconnected_at: Mapped[dt.datetime | None]
    paused_at: Mapped[dt.datetime | None]
    last_synced_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class MessageTemplate(Base):
    """One catalogue template version in one language, as created in the school's WABA."""

    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    template_key: Mapped[str] = mapped_column(Text)
    template_version: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(Text)
    provider_name: Mapped[str] = mapped_column(Text)
    provider_template_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    provider_category: Mapped[str | None] = mapped_column(Text)
    paused_by_sos: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class MessagingSettings(Base):
    """The school's messaging switches and times (one row per school)."""

    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    notices_enabled: Mapped[bool] = mapped_column(Boolean)
    absence_alerts_enabled: Mapped[bool] = mapped_column(Boolean)
    fee_reminders_enabled: Mapped[bool] = mapped_column(Boolean)
    fee_receipts_enabled: Mapped[bool] = mapped_column(Boolean)
    absence_alert_time: Mapped[dt.time] = mapped_column(Time)
    quiet_start: Mapped[dt.time] = mapped_column(Time)
    quiet_end: Mapped[dt.time] = mapped_column(Time)
    daily_cap: Mapped[int | None] = mapped_column(Integer)
    office_phone: Mapped[str | None] = mapped_column(Text)
    # 0061_messaging_sends: IST dates without automatic messages (US-2205 AC2).
    no_message_dates: Mapped[list[dt.date]] = mapped_column(
        ARRAY(Date), server_default=text("'{}'")
    )
    updated_by: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Optin(Base):
    """One opt-in or opt-out decision (append-only; the highest ``seq`` per guardian and
    category is the current state; no row = not asked)."""

    __tablename__ = "optins"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "guardian_id", "category", "seq"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    guardian_id: Mapped[uuid.UUID]
    category: Mapped[str] = mapped_column(Text)
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    wording_version: Mapped[str] = mapped_column(Text)
    decided_on: Mapped[dt.date] = mapped_column(Date)
    evidence_document_id: Mapped[uuid.UUID | None]
    phone_bidx: Mapped[bytes | None] = mapped_column(LargeBinary)
    phone_key_version: Mapped[int | None] = mapped_column(Integer)
    recorded_by: Mapped[uuid.UUID | None]
    recorded_by_membership: Mapped[uuid.UUID | None]
    recorded_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class JoinCode(Base):
    """A printed join-slip code (only its SHA-256 is stored; single use; 30 days)."""

    __tablename__ = "join_codes"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    code_hash: Mapped[bytes] = mapped_column(LargeBinary)
    guardian_id: Mapped[uuid.UUID]
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text))
    expires_at: Mapped[dt.datetime]
    used_at: Mapped[dt.datetime | None]
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class Message(Base):
    """One message to one guardian (no text: template, version, language and encrypted
    variables only; the phone as its blind index and last four digits)."""

    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    purpose: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    template_key: Mapped[str | None] = mapped_column(Text)
    template_version: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(Text)
    language_fallback: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    guardian_id: Mapped[uuid.UUID | None]
    student_id: Mapped[uuid.UUID | None]
    source_type: Mapped[str] = mapped_column(Text)
    source_id: Mapped[uuid.UUID]
    dedupe_key: Mapped[str] = mapped_column(Text)
    phone_bidx: Mapped[bytes | None] = mapped_column(LargeBinary)
    phone_key_version: Mapped[int | None] = mapped_column(Integer)
    phone_last4: Mapped[str | None] = mapped_column(Text)
    vars_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    public_handle_id: Mapped[uuid.UUID | None]
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    skip_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    billable: Mapped[bool | None] = mapped_column(Boolean)
    pricing_category: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    sent_at: Mapped[dt.datetime | None]
    delivered_at: Mapped[dt.datetime | None]
    read_at: Mapped[dt.datetime | None]
    failed_at: Mapped[dt.datetime | None]
    status_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    # 0061_messaging_sends: the notice broadcast it belongs to; when the sender may try again.
    broadcast_id: Mapped[uuid.UUID | None]
    next_attempt_at: Mapped[dt.datetime | None]


class InboundEvent(Base):
    """An inbound message's kind and time only: the text is never stored (FR-MSG-018)."""

    __tablename__ = "inbound_events"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    phone_bidx: Mapped[bytes] = mapped_column(LargeBinary)
    phone_key_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[dt.datetime]
    replied: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class ProviderEvent(Base):
    """The webhook inbox: one stored event per dedupe key, encrypted payload (FR-MSG-020)."""

    __tablename__ = "provider_events"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    provider: Mapped[str] = mapped_column(Text)
    account_id: Mapped[uuid.UUID]
    event_key: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    event_at: Mapped[dt.datetime | None]
    received_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    payload_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[dt.datetime | None]


__all__ = [
    "SCHEMA",
    "Account",
    "InboundEvent",
    "JoinCode",
    "Message",
    "MessageTemplate",
    "MessagingSettings",
    "Optin",
    "ProviderEvent",
]
