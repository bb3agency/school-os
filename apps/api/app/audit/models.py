"""SQLAlchemy Core tables for the audit schema (DDL in migrations 0002_audit and, for
``chain_verifications``, 0047_security_decisions)."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    LargeBinary,
    MetaData,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

events = Table(
    "events",
    metadata,
    Column("id", Uuid, nullable=False),
    Column("tenant_id", Uuid, primary_key=True),
    Column("seq", BigInteger, primary_key=True),
    Column("occurred_at", DateTime(timezone=True), primary_key=True),
    Column("actor_type", Text, nullable=False),
    Column("actor_id", Uuid),
    Column("action", Text, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("resource_id", Uuid),
    Column("summary", JSONB, nullable=False),
    Column("request_id", Text),
    Column("ip_hash", LargeBinary),
    Column("prev_hash", LargeBinary, nullable=False),
    Column("hash", LargeBinary, nullable=False),
    schema="audit",
)

chain_heads = Table(
    "chain_heads",
    metadata,
    Column("tenant_id", Uuid, primary_key=True),
    Column("last_seq", BigInteger, nullable=False),
    Column("last_hash", LargeBinary, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema="audit",
)

# Latest stored verification result and checkpoint per school (audit 2026-10-06 R-19).
chain_verifications = Table(
    "chain_verifications",
    metadata,
    Column("tenant_id", Uuid, primary_key=True),
    Column("verified_at", DateTime(timezone=True)),
    Column("mode", Text),
    Column("source", Text),
    Column("ok", Boolean),
    Column("checked", BigInteger, nullable=False),
    Column("first_bad_seq", BigInteger),
    Column("reason", Text),
    Column("checkpoint_seq", BigInteger, nullable=False),
    Column("checkpoint_hash", LargeBinary),
    Column("checkpoint_at", DateTime(timezone=True)),
    Column("last_full_at", DateTime(timezone=True)),
    Column("requested_at", DateTime(timezone=True)),
    Column("requested_by", Uuid),
    Column("requested_full", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema="audit",
)

platform_events = Table(
    "audit_events",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("seq", BigInteger, nullable=False, unique=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("actor_type", Text, nullable=False),
    Column("actor_id", Uuid),
    Column("action", Text, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("resource_id", Uuid),
    Column("subject_tenant_id", Uuid),
    Column("summary", JSONB, nullable=False),
    Column("request_id", Text),
    Column("ip_hash", LargeBinary),
    Column("prev_hash", LargeBinary, nullable=False),
    Column("hash", LargeBinary, nullable=False),
    schema="platform",
)

platform_chain_head = Table(
    "audit_chain_head",
    metadata,
    Column("id", Boolean, primary_key=True),
    Column("last_seq", BigInteger, nullable=False),
    Column("last_hash", LargeBinary, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema="platform",
)

EVENT_COLUMNS = tuple(c.name for c in events.columns)
PLATFORM_EVENT_COLUMNS = tuple(c.name for c in platform_events.columns)
