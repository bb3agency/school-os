"""Disclosure tables (migration 0065_disclosure; docs/05 §16.4; ADR-0051).

Describes the tables for typed queries only; DDL (RLS, column grants, the snapshot guard
trigger, the composite FKs into ``kb`` and ``core.public_handles``) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Integer, LargeBinary, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "ops"


class DisclosureDraft(Base):
    """One per school: typed inputs, chosen documents and the current public link."""

    __tablename__ = "disclosure_drafts"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    format_key: Mapped[str] = mapped_column(Text)
    format_version: Mapped[int] = mapped_column(Integer)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    documents: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    link_handle_id: Mapped[uuid.UUID | None]
    link_key: Mapped[str | None] = mapped_column(Text)
    link_created_at: Mapped[dt.datetime | None]
    updated_by: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class DisclosureSnapshot(Base):
    """One publication: frozen content; only withdrawal, views and file columns change."""

    __tablename__ = "disclosure_snapshots"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    format_key: Mapped[str] = mapped_column(Text)
    format_version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    published_by: Mapped[uuid.UUID]
    published_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    withdrawn_at: Mapped[dt.datetime | None]
    withdrawn_by: Mapped[uuid.UUID | None]
    withdraw_reason: Mapped[str | None] = mapped_column(Text)
    views_by_day: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    pdf_status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    html_status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    pdf_key: Mapped[str | None] = mapped_column(Text)
    html_key: Mapped[str | None] = mapped_column(Text)
    files_rendered_at: Mapped[dt.datetime | None]
    files_error: Mapped[str | None] = mapped_column(Text)


class DisclosureSnapshotDocument(Base):
    """A document version one snapshot published; ``n`` is its number in the public URL."""

    __tablename__ = "disclosure_snapshot_documents"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    snapshot_id: Mapped[uuid.UUID]
    n: Mapped[int] = mapped_column(Integer)
    item_key: Mapped[str] = mapped_column(Text)
    document_id: Mapped[uuid.UUID]
    version_id: Mapped[uuid.UUID]
    title: Mapped[str] = mapped_column(Text)


__all__ = ["DisclosureDraft", "DisclosureSnapshot", "DisclosureSnapshotDocument"]
