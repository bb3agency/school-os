"""SQLAlchemy Core tables for schema ``ops`` (DDL in migration 0006_ops). All tenant-owned."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, LargeBinary, MetaData, Table, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="ops")

job_runs = Table(
    "job_runs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("task_name", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("progress", JSONB),
    Column("error", Text),
    Column("created_by", Uuid),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)

outbox = Table(
    "outbox",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("dispatched_at", DateTime(timezone=True)),
)

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("tenant_id", Uuid, primary_key=True),
    Column("user_id", Uuid, primary_key=True),
    Column("key", Text, primary_key=True),
    Column("method", Text, nullable=False),
    Column("route", Text, nullable=False),
    Column("request_sha256", LargeBinary, nullable=False),
    Column("status", Text, nullable=False),
    Column("response_status", Integer),
    Column("resource_type", Text),
    Column("resource_id", Uuid),
    Column("location", Text),
    Column("created_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True)),
)
