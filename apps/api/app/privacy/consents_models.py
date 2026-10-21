"""Consent register tables in schema ``sis`` (migration 0067_consents_breach; docs/05 §16.2;
ADR-0049).

Models describe tables for typed queries only; DDL (RLS, grants, triggers, FKs into
``sis.students``, ``sis.guardians``, ``kb.documents``, ``core.memberships``) lives in the
migration, so this module never imports another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, Date, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "sis"

CONSENT_STATUSES = ("given", "refused", "pending", "withdrawn")


class ConsentPurpose(Base):
    """One optional purpose of the school (its text is frozen once answered; a change bumps
    ``form_version``)."""

    __tablename__ = "consent_purposes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    key: Mapped[str] = mapped_column(Text)
    label_en: Mapped[str] = mapped_column(Text)
    label_te: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    form_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    suggested_key: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Consent(Base):
    """One recorded decision (append-only; the highest ``seq`` per student and purpose is the
    current one)."""

    __tablename__ = "consents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "student_id", "purpose_id", "seq"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    purpose_id: Mapped[uuid.UUID]
    form_version: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    relationship: Mapped[str | None] = mapped_column(Text)
    guardian_id: Mapped[uuid.UUID | None]
    decided_on: Mapped[dt.date | None] = mapped_column(Date)
    form_language: Mapped[str | None] = mapped_column(Text)
    evidence_document_id: Mapped[uuid.UUID | None]
    note: Mapped[str | None] = mapped_column(Text)
    privacy_request_id: Mapped[uuid.UUID | None]
    recorded_by: Mapped[uuid.UUID]
    recorded_by_membership: Mapped[uuid.UUID]
    recorded_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
