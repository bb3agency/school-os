"""Circulars, tasks and notices tables (migration 0034_circulars; docs/05 §6.3).

Typed mappings for queries only; DDL (RLS, CHECKs, composite FKs, column grants, the offboarding
purge policy) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Boolean, Date, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base


class CircularReading(Base):
    __tablename__ = "circular_readings"
    __table_args__ = {"schema": "kb"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    document_id: Mapped[uuid.UUID]
    version_id: Mapped[uuid.UUID]
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    error_code: Mapped[str | None] = mapped_column(Text)
    issuer: Mapped[str | None] = mapped_column(Text)
    reference_no: Mapped[str | None] = mapped_column(Text)
    issued_on: Mapped[dt.date | None] = mapped_column(Date)
    subject: Mapped[str | None] = mapped_column(Text)
    summary_en: Mapped[str | None] = mapped_column(Text)
    summary_te: Mapped[str | None] = mapped_column(Text)
    summary_sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    model: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str | None] = mapped_column(Text)
    suggestions_dropped: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    passages_sent: Mapped[int | None] = mapped_column(Integer)
    passages_total: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    requested_by: Mapped[uuid.UUID | None]
    reviewed_by: Mapped[uuid.UUID | None]
    reviewed_at: Mapped[dt.datetime | None]
    started_at: Mapped[dt.datetime | None]
    completed_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class CircularSuggestion(Base):
    __tablename__ = "circular_suggestions"
    __table_args__ = {"schema": "kb"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    reading_id: Mapped[uuid.UUID]
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    details: Mapped[str | None] = mapped_column(Text)
    due_on: Mapped[dt.date] = mapped_column(Date)
    citation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default=text("'suggested'"))
    task_id: Mapped[uuid.UUID | None]
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    title: Mapped[str] = mapped_column(Text)
    details: Mapped[str | None] = mapped_column(Text)
    owner_membership_id: Mapped[uuid.UUID]
    due_on: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    source: Mapped[str] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None]
    citation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID]
    created_by_membership: Mapped[uuid.UUID]
    completed_by: Mapped[uuid.UUID | None]
    completed_at: Mapped[dt.datetime | None]
    cancelled_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class ParentNotice(Base):
    __tablename__ = "parent_notices"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    source: Mapped[str] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(Text, server_default=text("'draft'"))
    ai_drafted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    draft_error: Mapped[str | None] = mapped_column(Text)
    title_en: Mapped[str] = mapped_column(Text, server_default=text("''"))
    body_en: Mapped[str] = mapped_column(Text, server_default=text("''"))
    title_te: Mapped[str] = mapped_column(Text, server_default=text("''"))
    body_te: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_by: Mapped[uuid.UUID]
    approved_by: Mapped[uuid.UUID | None]
    approved_at: Mapped[dt.datetime | None]
    render_status: Mapped[str | None] = mapped_column(Text)
    render_error: Mapped[str | None] = mapped_column(Text)
    pdf_key: Mapped[str | None] = mapped_column(Text)
    png_key: Mapped[str | None] = mapped_column(Text)
    rendered_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


__all__ = ["CircularReading", "CircularSuggestion", "ParentNotice", "Task"]
