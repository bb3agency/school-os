"""SQLAlchemy Core table for ``ops.break_glass_grants`` (DDL: 0006_ops + 0011_breakglass)."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, Table, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="ops")

grants = Table(
    "break_glass_grants",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("platform_user_id", Uuid, nullable=False),
    Column("platform_request_id", Uuid),
    Column("approved_by_membership", Uuid),
    Column("reason", Text, nullable=False),
    Column("scope", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("starts_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
    Column("reason_code", Text, nullable=False),
    Column("duration_minutes", Integer),
    Column("emergency", Boolean, nullable=False),
    Column("operator_display_name", Text),
    Column("requested_at", DateTime(timezone=True)),
    Column("decided_at", DateTime(timezone=True)),
    Column("membership_id", Uuid),
    Column("denied_by_membership", Uuid),
    Column("revoked_by_membership", Uuid),
    Column("platform_status_synced", Text),
)
