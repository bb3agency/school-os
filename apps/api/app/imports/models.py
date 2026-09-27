"""Import tables in schema ``sis`` (migration 0012_imports, docs/05 §5.2).

Typed mappings for queries only; DDL (RLS, CHECKs, composite FKs to kb.documents, ops.job_runs,
sis.students) lives in the migration, so this module never imports another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKeyConstraint, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "sis"


class ImportMappingTemplate(Base):
    __tablename__ = "import_mapping_templates"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    header_signature: Mapped[str] = mapped_column(Text)
    headers: Mapped[list[str]] = mapped_column(JSONB)
    mapping: Mapped[dict[str, str]] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    last_used_at: Mapped[dt.datetime | None]
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text, server_default=text("'spreadsheet'"))
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'uploaded'"))
    document_id: Mapped[uuid.UUID | None]
    file_kind: Mapped[str | None] = mapped_column(Text)
    header_row: Mapped[int | None] = mapped_column(Integer)
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    mapping: Mapped[dict[str, str]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    mapping_template_id: Mapped[uuid.UUID | None]
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    row_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None]
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    committed_at: Mapped[dt.datetime | None]
    committed_by: Mapped[uuid.UUID | None]
    revert_deadline: Mapped[dt.datetime | None]
    reverted_at: Mapped[dt.datetime | None]
    reverted_by: Mapped[uuid.UUID | None]
    raw_file_deleted_at: Mapped[dt.datetime | None]
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            ["sis.import_batches.tenant_id", "sis.import_batches.id"],
            ondelete="CASCADE",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    batch_id: Mapped[uuid.UUID]
    row_no: Mapped[int] = mapped_column(Integer)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSONB)
    errors: Mapped[list[dict[str, str]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    warnings: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(Text)
    student_id: Mapped[uuid.UUID | None]
    created_student: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    student_version: Mapped[int | None] = mapped_column(Integer)
