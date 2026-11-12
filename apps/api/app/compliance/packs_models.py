"""Inspection pack log in schema ``ops`` (migration 0098_inspection_packs; docs/05 §16.9;
FC-07, FO-12).

Models describe tables for typed queries only; DDL (RLS, grants, the composite FK into
``core.academic_years``) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Boolean, Date, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "ops"


class InspectionPack(Base):
    """One generated inspection pack: what it held when it was made (append-only)."""

    __tablename__ = "inspection_packs"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    pack_key: Mapped[str] = mapped_column(Text)
    pack_version: Mapped[int] = mapped_column(Integer)
    pack_label: Mapped[str] = mapped_column(Text)
    authority: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean)
    academic_year_id: Mapped[uuid.UUID | None]
    visit_on: Mapped[dt.date | None] = mapped_column(Date)
    registers: Mapped[list[str]] = mapped_column(ARRAY(Text))
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    counts: Mapped[dict[str, int]] = mapped_column(JSONB)
    generated_by: Mapped[uuid.UUID]
    generated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
