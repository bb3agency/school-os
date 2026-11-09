"""DPDP readiness marks and notice given at admission (migration 0083_dpdp_readiness; FC-03;
FR-DPDP-053..FR-DPDP-060; docs/05 §16.1).

Models describe tables for typed queries only; DDL (RLS, grants, append-only triggers, composite
FKs into ``sis.students``, ``kb.documents``, ``ops.privacy_notices``) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Date, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "ops"


class ReadinessMark(Base):
    """One readiness step the school recorded (append-only; the latest per item counts)."""

    __tablename__ = "privacy_readiness_marks"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    item: Mapped[str] = mapped_column(Text)
    done_on: Mapped[dt.date] = mapped_column(Date)
    document_id: Mapped[uuid.UUID | None]
    recorded_by: Mapped[uuid.UUID]
    recorded_by_membership: Mapped[uuid.UUID]
    recorded_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class AdmissionNotice(Base):
    """The privacy notice given to one student's family at admission (append-only, one per
    student)."""

    __tablename__ = "privacy_admission_notices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "student_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    given_on: Mapped[dt.date] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(Text)
    notice_id: Mapped[uuid.UUID | None]
    recorded_by: Mapped[uuid.UUID]
    recorded_by_membership: Mapped[uuid.UUID]
    recorded_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
