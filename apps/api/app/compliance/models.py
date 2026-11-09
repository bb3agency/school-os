"""Compliance certificate table in schema ``ops`` (migration 0088_compliance_register;
docs/05 §16.9; FC-01).

Models describe tables for typed queries only; DDL (RLS, column grants, the composite FKs into
``kb.documents`` and ``core.memberships``, the one-current-per-kind index) lives in the
migration.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Date, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "ops"


class ComplianceCertificate(Base):
    """One certificate the school holds or held (a renewal is a new row; the old one is kept)."""

    __tablename__ = "compliance_certificates"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    reference_no: Mapped[str | None] = mapped_column(Text)
    issued_by: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[dt.date | None] = mapped_column(Date)
    valid_until: Mapped[dt.date | None] = mapped_column(Date)
    document_id: Mapped[uuid.UUID | None]
    owner_membership_id: Mapped[uuid.UUID | None]
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'current'"))
    superseded_by: Mapped[uuid.UUID | None]
    superseded_at: Mapped[dt.datetime | None]
    removed_by: Mapped[uuid.UUID | None]
    removed_at: Mapped[dt.datetime | None]
    last_reminded_lead: Mapped[int | None] = mapped_column(Integer)
    last_reminded_on: Mapped[dt.date | None] = mapped_column(Date)
    recorded_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
