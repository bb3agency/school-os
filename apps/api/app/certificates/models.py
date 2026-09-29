"""``sis.certificates`` and ``sis.certificate_counters`` (migration 0033_certificates, docs/05
§5.7).

Describes the tables for typed queries only; DDL (RLS, CHECKs, the frozen-row and append-only
triggers, column grants) lives in the migration. FKs into other modules' tables are enforced by
the database and not declared here, so this module never imports another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Integer, LargeBinary, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

STATUSES = ("pending", "issued", "rejected", "withdrawn", "cancelled")
PDF_STATUSES = ("none", "queued", "ready", "failed")


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": "sis"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    certificate_type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    original_certificate_id: Mapped[uuid.UUID | None]
    duplicate_reason: Mapped[str | None] = mapped_column(Text)
    duplicate_no: Mapped[int | None] = mapped_column(Integer)
    academic_year_id: Mapped[uuid.UUID | None]
    serial_no: Mapped[int | None] = mapped_column(Integer)
    serial: Mapped[str | None] = mapped_column(Text)
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    content_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    template_version: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID]
    requested_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[dt.datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)
    issued_by: Mapped[uuid.UUID | None]
    issued_at: Mapped[dt.datetime | None]
    cancelled_by: Mapped[uuid.UUID | None]
    cancelled_at: Mapped[dt.datetime | None]
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None]
    pdf_status: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    pdf_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class CertificateCounter(Base):
    __tablename__ = "certificate_counters"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": "sis"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    certificate_type: Mapped[str] = mapped_column(Text)
    academic_year_id: Mapped[uuid.UUID]
    last_no: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
