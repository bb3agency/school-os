"""Document tables in schema ``kb`` (migration 0009_kb_documents, docs/05 §6).

Typed mappings for queries only; DDL (RLS, CHECKs, composite FKs) lives in the migration. The
composite FK to ``core.academic_years`` is enforced by the database only, so this module never
imports another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, Date, ForeignKeyConstraint, Integer, LargeBinary, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "kb"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    purpose: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str | None] = mapped_column(Text)
    issued_on: Mapped[dt.date | None] = mapped_column(Date)
    academic_year_id: Mapped[uuid.UUID | None]
    language: Mapped[str | None] = mapped_column(Text)
    sensitivity: Mapped[str] = mapped_column(Text)
    current_version_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"], ["kb.documents.tenant_id", "kb.documents.id"]
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    version_no: Mapped[int] = mapped_column(Integer)
    object_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[bytes] = mapped_column(LargeBinary)
    mime_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class DocumentAcl(Base):
    __tablename__ = "document_acl"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["kb.documents.tenant_id", "kb.documents.id"],
            ondelete="CASCADE",
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    principal_type: Mapped[str] = mapped_column(Text, primary_key=True)
    principal_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class UploadIntent(Base):
    __tablename__ = "upload_intents"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    purpose: Mapped[str] = mapped_column(Text)
    document_id: Mapped[uuid.UUID]
    version_no: Mapped[int] = mapped_column(Integer)
    batch_id: Mapped[uuid.UUID | None]
    object_key: Mapped[str] = mapped_column(Text)
    declared_content_type: Mapped[str] = mapped_column(Text)
    declared_size: Mapped[int] = mapped_column(BigInteger)
    max_bytes: Mapped[int] = mapped_column(BigInteger)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    expires_at: Mapped[dt.datetime]
    consumed_at: Mapped[dt.datetime | None]
