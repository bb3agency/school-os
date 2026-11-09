"""``fin.fee_addons`` and ``fin.fee_addon_assignments`` (migration 0088_fee_addons; FM-02;
FR-FEE-081..100).

Describes the tables for typed queries only; DDL (RLS, CHECKs, the frozen trigger, the one active
add-on per head index, column grants) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, Integer, Text, UniqueConstraint, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base
from app.fees.models import SCHEMA


class FeeAddon(Base):
    __tablename__ = "fee_addons"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    academic_year_id: Mapped[uuid.UUID]
    head_id: Mapped[uuid.UUID]
    label: Mapped[str] = mapped_column(Text)
    amounts: Mapped[dict[str, str]] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("true"))
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


class FeeAddonAssignment(Base):
    __tablename__ = "fee_addon_assignments"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    addon_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    head_id: Mapped[uuid.UUID]
    start_instalment: Mapped[int] = mapped_column(Integer)
    end_instalment: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'active'"))
    assigned_by: Mapped[uuid.UUID]
    assigned_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    ended_by: Mapped[uuid.UUID | None]
    ended_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))
