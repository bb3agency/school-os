"""Legal hold table in schema ``ops`` (migration 0087_legal_holds; docs/05 §16.4; ADR-0059).

Models describe tables for typed queries only; DDL (RLS, grants, the two-people CHECKs, the
composite FK into ``sis.students``) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Date, Integer, LargeBinary, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "ops"


class LegalHold(Base):
    """One legal hold: on one student or on a whole record class."""

    __tablename__ = "legal_holds"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    scope: Mapped[str] = mapped_column(Text)
    student_id: Mapped[uuid.UUID | None]
    record_class: Mapped[str | None] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(Text)
    reference_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    review_on: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    placed_by: Mapped[uuid.UUID]
    placed_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    approved_by: Mapped[uuid.UUID | None]
    approved_at: Mapped[dt.datetime | None]
    rejected_by: Mapped[uuid.UUID | None]
    rejected_at: Mapped[dt.datetime | None]
    lift_requested_by: Mapped[uuid.UUID | None]
    lift_requested_at: Mapped[dt.datetime | None]
    lifted_by: Mapped[uuid.UUID | None]
    lifted_at: Mapped[dt.datetime | None]
    last_reminded_on: Mapped[dt.date | None] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
