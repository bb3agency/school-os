"""Tally connector tables (0036_tally, 0077_tally_bills; docs/05 §7.4; ADR-0032 Proposed).

Typed mappings for queries only; DDL (RLS, CHECKs, composite FKs, column grants, the offboarding
purge policy) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, Integer, LargeBinary, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base


class EnrolmentCode(Base):
    __tablename__ = "tally_enrolment_codes"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    code_hash: Mapped[bytes] = mapped_column(LargeBinary)
    device_name: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID]
    expires_at: Mapped[dt.datetime]
    used_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class Device(Base):
    __tablename__ = "tally_devices"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    enrolment_code_id: Mapped[uuid.UUID]
    key_id: Mapped[str | None] = mapped_column(Text)
    key_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    next_key_id: Mapped[str | None] = mapped_column(Text)
    next_key_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    rotation_started_at: Mapped[dt.datetime | None]
    agent_version: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(Text)
    tally_product: Mapped[str | None] = mapped_column(Text)
    enrolled_by: Mapped[uuid.UUID]
    enrolled_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    revoked_by: Mapped[uuid.UUID | None]
    revoked_at: Mapped[dt.datetime | None]
    last_seen_at: Mapped[dt.datetime | None]
    last_sync_at: Mapped[dt.datetime | None]
    silent_notified_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Group(Base):
    __tablename__ = "tally_groups"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    company: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    parent: Mapped[str | None] = mapped_column(Text)
    present: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    selected: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    selected_by: Mapped[uuid.UUID | None]
    selected_at: Mapped[dt.datetime | None]
    first_seen_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    last_seen_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Sync(Base):
    __tablename__ = "tally_syncs"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    device_id: Mapped[uuid.UUID]
    batch_id: Mapped[uuid.UUID]
    company: Mapped[str] = mapped_column(Text)
    as_of: Mapped[dt.date] = mapped_column(Date)
    groups: Mapped[int] = mapped_column(Integer)
    parties: Mapped[int] = mapped_column(Integer)
    created: Mapped[int] = mapped_column(Integer)
    updated: Mapped[int] = mapped_column(Integer)
    missing: Mapped[int] = mapped_column(Integer)
    total_due: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    received_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class Party(Base):
    __tablename__ = "tally_parties"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    company: Mapped[str] = mapped_column(Text)
    guid: Mapped[str | None] = mapped_column(Text)
    ledger_name: Mapped[str] = mapped_column(Text)
    group_name: Mapped[str] = mapped_column(Text)
    closing_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    as_of: Mapped[dt.date] = mapped_column(Date)
    present: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_sync_id: Mapped[uuid.UUID]
    first_seen_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class PartyLink(Base):
    __tablename__ = "tally_party_links"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    party_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    linked_by: Mapped[uuid.UUID]
    linked_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class Bill(Base):
    """A pending bill (Tally bill-wise allocation) of a party ledger, from the snapshot that last
    updated the ledger (0077_tally_bills; D63). Replaced as a whole per snapshot."""

    __tablename__ = "tally_bills"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    party_id: Mapped[uuid.UUID]
    sync_id: Mapped[uuid.UUID]
    reference: Mapped[str] = mapped_column(Text)
    bill_date: Mapped[dt.date | None] = mapped_column(Date)
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    pending: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class ExportSettings(Base):
    """The school's Tally ledger mapping for the voucher file (0088_tally_vouchers; FM-03)."""

    __tablename__ = "tally_export_settings"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    party_mode: Mapped[str] = mapped_column(Text)
    receivable_ledger: Mapped[str] = mapped_column(Text)
    mode_ledgers: Mapped[dict[str, Any]] = mapped_column(JSONB)
    head_ledgers: Mapped[dict[str, Any]] = mapped_column(JSONB)
    other_income_ledger: Mapped[str | None] = mapped_column(Text)
    include_demand: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    company: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class VoucherExport(Base):
    """One downloaded voucher file (0088_tally_vouchers). The file itself is never kept."""

    __tablename__ = "tally_voucher_exports"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    period_from: Mapped[dt.date] = mapped_column(Date)
    period_to: Mapped[dt.date] = mapped_column(Date)
    include_exported: Mapped[bool] = mapped_column(Boolean)
    include_demand: Mapped[bool] = mapped_column(Boolean)
    party_mode: Mapped[str] = mapped_column(Text)
    vouchers: Mapped[int] = mapped_column(Integer)
    new_vouchers: Mapped[int] = mapped_column(Integer)
    cash_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    file_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class ExportedVoucher(Base):
    """A source record's first export (``UNIQUE (tenant_id, voucher_key)``)."""

    __tablename__ = "tally_exported_vouchers"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    voucher_key: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    export_id: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


__all__ = [
    "Bill",
    "Device",
    "EnrolmentCode",
    "ExportSettings",
    "ExportedVoucher",
    "Group",
    "Party",
    "PartyLink",
    "Sync",
    "VoucherExport",
]
