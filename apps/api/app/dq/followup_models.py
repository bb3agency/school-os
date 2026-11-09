"""SQLAlchemy Core tables of the follow-up log (FO-01) and the UDISE+ to-do ticks (FO-03);
DDL in migration 0088_udise_followup (FR-UDI-021..FR-UDI-040)."""

from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, Integer, MetaData, Table, Text, Uuid

metadata = MetaData(schema="sis")

# Append-only: one row per step a person recorded while a fix waits outside the school.
dq_followups = Table(
    "dq_followups",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("student_id", Uuid, nullable=False),
    Column("profile_key", Text, nullable=False),
    Column("fix_owner", Text, nullable=False),
    Column("event", Text, nullable=False),
    Column("channel", Text),
    Column("occurred_on", Date, nullable=False),
    Column("promised_on", Date),
    Column("recorded_by", Uuid, nullable=False),
    Column("recorded_by_membership", Uuid, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)

# The clerk's tick on a computed UDISE+ to-do item (one row per item key).
udise_movement_marks = Table(
    "udise_movement_marks",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("item_key", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("student_id", Uuid),
    Column("state", Text, nullable=False),
    Column("marked_by", Uuid, nullable=False),
    Column("marked_by_membership", Uuid, nullable=False),
    Column("marked_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
)
