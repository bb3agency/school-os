"""Tally connector tables (migration 0036_tally; docs/05 §7.4; ADR-0032 Proposed).

Typed mappings for queries only; DDL (RLS, CHECKs, composite FKs, column grants, the offboarding
purge policy) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Date, Integer, LargeBinary, Numeric, Text, text
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


__all__ = ["Device", "EnrolmentCode", "Group", "Party", "PartyLink", "Sync"]
