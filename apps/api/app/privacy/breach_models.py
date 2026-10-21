"""Breach register tables in schema ``ops`` (migration 0067_consents_breach; docs/05 §16.3;
ADR-0050).

Models describe tables for typed queries only; DDL (RLS, grants, FKs into ``sis.students``,
``sis.guardians``, ``core.memberships``) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Integer, LargeBinary, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "ops"


class BreachRecord(Base):
    """One breach the school knows of: its own, or SchoolOS's notice (``processor_notice``)."""

    __tablename__ = "breach_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "platform_incident_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    origin: Mapped[str] = mapped_column(Text)
    platform_incident_id: Mapped[uuid.UUID | None]
    noticed_at: Mapped[dt.datetime]
    description_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    containment_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text))
    approx_children: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    approx_guardians: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    approx_staff: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    processor_facts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    board_notice_done_at: Mapped[dt.datetime | None]
    board_notice_method: Mapped[str | None] = mapped_column(Text)
    board_report_done_at: Mapped[dt.datetime | None]
    board_report_method: Mapped[str | None] = mapped_column(Text)
    people_notified_at: Mapped[dt.datetime | None]
    people_notified_method: Mapped[str | None] = mapped_column(Text)
    close_reason: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[dt.datetime | None]
    recorded_by: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class BreachAffected(Base):
    """One affected student, guardian or staff member of a breach (ids only)."""

    __tablename__ = "breach_affected"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    breach_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID | None]
    guardian_id: Mapped[uuid.UUID | None]
    membership_id: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class BreachReminder(Base):
    """A reminder already sent for a breach (idempotency of FR-DPDP-035)."""

    __tablename__ = "breach_reminders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "breach_id", "reminder"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    breach_id: Mapped[uuid.UUID]
    reminder: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
