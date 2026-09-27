"""Extraction tables in schema ``sis`` (migration 0016_extraction, docs/05 §5).

Typed mappings for queries only; DDL (RLS, CHECKs, composite FKs, triggers) lives in the
migration. FKs to ``kb.documents``, ``sis.students`` and ``core.memberships`` are enforced by the
database only, so this module never imports another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ARRAY, Boolean, ForeignKeyConstraint, Integer, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "sis"


class ExtractionBatch(Base):
    __tablename__ = "extraction_batches"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    source: Mapped[str] = mapped_column(Text, server_default=text("'admission_register'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    provider: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID]
    created_by_membership: Mapped[uuid.UUID]
    page_count: Mapped[int] = mapped_column(Integer)
    pages_done: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    pages_failed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    pages_withheld: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    items_total: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    items_pending: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    items_confirmed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    items_rejected: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    items_low_confidence: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    processed_at: Mapped[dt.datetime | None]
    completed_at: Mapped[dt.datetime | None]
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class ExtractionPage(Base):
    __tablename__ = "extraction_pages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            ["sis.extraction_batches.tenant_id", "sis.extraction_batches.id"],
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    batch_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    document_version_no: Mapped[int] = mapped_column(Integer)
    page_no: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    error_code: Mapped[str | None] = mapped_column(Text)
    aadhaar_detected: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    image_withheld: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    row_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    low_confidence_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    dropped_field_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    processed_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class ExtractionItem(Base):
    __tablename__ = "extraction_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            ["sis.extraction_batches.tenant_id", "sis.extraction_batches.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "page_id"],
            ["sis.extraction_pages.tenant_id", "sis.extraction_pages.id"],
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    batch_id: Mapped[uuid.UUID]
    page_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    page_no: Mapped[int] = mapped_column(Integer)
    row_index: Mapped[int] = mapped_column(Integer)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB)
    low_confidence: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    masked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending_review'"))
    reviewed_by: Mapped[uuid.UUID | None]
    reviewed_at: Mapped[dt.datetime | None]
    reject_reason: Mapped[str | None] = mapped_column(Text)
    student_id: Mapped[uuid.UUID | None]
    value_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid(as_uuid=True)), server_default=text("'{}'")
    )
    corrected_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    created_student: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
