"""Privacy request register tables in schema ``ops`` (migration 0066_privacy_requests; docs/05
§16.1; ADR-0048).

Models describe tables for typed queries only; DDL (RLS, grants, triggers, composite FKs into
``sis.students``, ``sis.guardians``, ``core.memberships``, ``kb.documents``, ``ops.exports``) lives
in the migration, so this module never imports another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Date, Integer, LargeBinary, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "ops"


class PrivacyNotice(Base):
    """One notice issue the school recorded (append-only)."""

    __tablename__ = "privacy_notices"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    template_key: Mapped[str] = mapped_column(Text)
    template_version: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    issued_on: Mapped[dt.date] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None]
    recorded_by: Mapped[uuid.UUID]
    recorded_by_membership: Mapped[uuid.UUID]
    recorded_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class PrivacyRequest(Base):
    """One data-principal request or grievance."""

    __tablename__ = "privacy_requests"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    reference: Mapped[str] = mapped_column(Text)
    year: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    subject_kind: Mapped[str] = mapped_column(Text)
    student_id: Mapped[uuid.UUID | None]
    guardian_id: Mapped[uuid.UUID | None]
    membership_id: Mapped[uuid.UUID | None]
    requester_relationship: Mapped[str] = mapped_column(Text)
    verification_method: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[dt.datetime | None]
    received_on: Mapped[dt.date] = mapped_column(Date)
    due_on: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default=text("'received'"))
    requester_name_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    request_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    notes_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    nominee_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    responded_on: Mapped[dt.date | None] = mapped_column(Date)
    nomination_withdrawn_at: Mapped[dt.datetime | None]
    report_export_id: Mapped[uuid.UUID | None]
    report_released_by: Mapped[uuid.UUID | None]
    report_released_at: Mapped[dt.datetime | None]
    logged_by: Mapped[uuid.UUID]
    logged_by_membership: Mapped[uuid.UUID]
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[dt.datetime | None]
    closed_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class PrivacyRequestEvent(Base):
    """One step of a request's timeline (append-only; ids, codes and counts only)."""

    __tablename__ = "privacy_request_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "request_id", "seq"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    request_id: Mapped[uuid.UUID]
    seq: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    actor_user_id: Mapped[uuid.UUID | None]
    actor_membership_id: Mapped[uuid.UUID | None]
    at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class PrivacyRequestLink(Base):
    """A change request, consent or export a request led to (insert-only)."""

    __tablename__ = "privacy_request_links"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    request_id: Mapped[uuid.UUID]
    link_kind: Mapped[str] = mapped_column(Text)
    target_id: Mapped[uuid.UUID]
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class PrivacyRequestCounter(Base):
    """Last reference number per school and calendar year (gap-free, never down)."""

    __tablename__ = "privacy_request_counters"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    year: Mapped[int] = mapped_column(Integer)
    last_no: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
