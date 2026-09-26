"""SQLAlchemy Core table for ``ops.notifications`` (DDL in migration 0010_notifications)."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, MetaData, Table, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="ops")

notifications = Table(
    "notifications",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("recipient_membership_id", Uuid, nullable=False),
    Column("template_key", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("resource_type", Text),
    Column("resource_id", Uuid),
    Column("dedupe_key", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("read_at", DateTime(timezone=True)),
)
