"""Safety check log and policy library tables in schema ``ops`` (migration
0098_drills_policies; docs/05 §16.10; FC-09, FC-10).

Models describe tables for typed queries only; DDL (RLS, column grants, composite FKs, the
append-only trigger on acknowledgements) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Date, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "ops"


class ComplianceCheck(Base):
    """One drill or periodic check the school logged (counts and a short note only)."""

    __tablename__ = "compliance_checks"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    done_on: Mapped[dt.date] = mapped_column(Date)
    led_by_membership_id: Mapped[uuid.UUID | None]
    participants_count: Mapped[int | None] = mapped_column(Integer)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(Text, server_default=text("'ok'"))
    observations: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(Text, server_default=text("'recorded'"))
    removed_by: Mapped[uuid.UUID | None]
    removed_at: Mapped[dt.datetime | None]
    last_reminded_lead: Mapped[int | None] = mapped_column(Integer)
    last_reminded_on: Mapped[dt.date | None] = mapped_column(Date)
    recorded_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class CompliancePolicy(Base):
    """A school policy (a document of purpose ``policy``) with its review date and the roles
    whose members must acknowledge it."""

    __tablename__ = "compliance_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    title: Mapped[str] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None]
    ack_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    review_due: Mapped[dt.date] = mapped_column(Date)
    last_reviewed_on: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    retired_by: Mapped[uuid.UUID | None]
    retired_at: Mapped[dt.datetime | None]
    ack_requested_at: Mapped[dt.datetime | None]
    last_reminded_lead: Mapped[int | None] = mapped_column(Integer)
    last_reminded_on: Mapped[dt.date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class CompliancePolicyAck(Base):
    """A member's acknowledgement of one exact version of a policy (append-only)."""

    __tablename__ = "compliance_policy_acks"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    policy_id: Mapped[uuid.UUID]
    document_version_id: Mapped[uuid.UUID]
    membership_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    acknowledged_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
