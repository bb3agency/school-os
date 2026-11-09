"""SQLAlchemy Core tables of the weekly brief (DDL in migration 0088_weekly_brief)."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, MetaData, Table, Uuid
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="ops")

weekly_briefs = Table(
    "weekly_briefs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("week_start", Date, nullable=False),
    Column("counts", JSONB, nullable=False),
    Column("notified", Integer, nullable=False),
    Column("built_at", DateTime(timezone=True), nullable=False),
)

brief_preferences = Table(
    "brief_preferences",
    metadata,
    Column("tenant_id", Uuid, primary_key=True),
    Column("membership_id", Uuid, primary_key=True),
    Column("in_app", Boolean, nullable=False),
    Column("email", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
)
