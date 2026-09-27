"""``sis.change_requests`` (migration 0014_change_requests, docs/05 §5).

Describes the table for typed queries only; DDL (RLS, CHECKs, the frozen-row trigger, column
grants) lives in the migration. FKs into other modules' tables are enforced by the database and
not declared here, so this module never imports another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Date, Integer, LargeBinary, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

STATUSES = ("pending", "approved", "rejected", "expired", "cancelled")


class ChangeRequest(Base):
    __tablename__ = "change_requests"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": "sis"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    attribute_key: Mapped[str] = mapped_column(Text)
    target_source: Mapped[str] = mapped_column(Text, server_default=text("'admission_register'"))
    old_value_id: Mapped[uuid.UUID | None]
    old_value_text: Mapped[str | None] = mapped_column(Text)
    old_value_date: Mapped[dt.date | None] = mapped_column(Date)
    old_value_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    new_value_text: Mapped[str | None] = mapped_column(Text)
    new_value_date: Mapped[dt.date | None] = mapped_column(Date)
    new_value_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    evidence_document_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    requested_by: Mapped[uuid.UUID]
    requested_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[dt.datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)
    applied_value_id: Mapped[uuid.UUID | None]
    expires_at: Mapped[dt.datetime]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
