"""APAAR consent register tables in schema ``sis`` (migration 0051_apaar_consent_pen; docs/05 §5;
ADR-0039).

Models describe tables for typed queries only; DDL (RLS, grants, FKs into ``sis.students``,
``sis.guardians``, ``kb.documents``, ``core.memberships``) lives in the migration, so this module
never imports another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Date, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "sis"

CONSENT_STATUSES = ("given", "refused", "pending", "withdrawn")
FORM_LANGUAGES = ("en", "te")


class ApaarConsent(Base):
    """One recorded decision (append-only; the highest ``seq`` per student is the current one)."""

    __tablename__ = "apaar_consents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "student_id", "seq"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    relationship: Mapped[str | None] = mapped_column(Text)
    guardian_id: Mapped[uuid.UUID | None]
    decided_on: Mapped[dt.date | None] = mapped_column(Date)
    form_language: Mapped[str | None] = mapped_column(Text)
    evidence_document_id: Mapped[uuid.UUID | None]
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID]
    recorded_by_membership: Mapped[uuid.UUID]
    recorded_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class ApaarConsentSettings(Base):
    """The school's choice of language for the printed parent form (owner decision D9)."""

    __tablename__ = "apaar_consent_settings"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    form_language: Mapped[str] = mapped_column(Text, server_default=text("'en'"))
    updated_by: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
