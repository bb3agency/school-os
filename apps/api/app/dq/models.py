"""SQLAlchemy Core tables for data-quality runs and findings (DDL in migration 0013_dq)."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, MetaData, Table, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

metadata = MetaData(schema="sis")

dq_runs = Table(
    "dq_runs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("event_type", Text),
    Column("scope", JSONB, nullable=False),
    Column("profile_key", Text),
    Column("status", Text, nullable=False),
    Column("started_by", Uuid),
    Column("requested_by_membership", Uuid),
    Column("created_at", DateTime(timezone=True)),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("stats", JSONB),
    Column("error_code", Text),
)

dq_findings = Table(
    "dq_findings",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("student_id", Uuid, nullable=False),
    Column("related_student_id", Uuid),
    Column("rule_id", Text, nullable=False),
    Column("rule_version", Integer, nullable=False),
    Column("profile_key", Text),
    Column("attribute_key", Text),
    Column("sources", ARRAY(Text), nullable=False),
    Column("match_class", Text),
    Column("severity", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("explanation_code", Text, nullable=False),
    Column("explanation_params", JSONB, nullable=False),
    Column("route_codes", ARRAY(Text), nullable=False),
    Column("details", JSONB, nullable=False),
    Column("conflict_hash", Text, nullable=False),
    Column("first_seen_run_id", Uuid),
    Column("last_seen_run_id", Uuid),
    Column("first_seen_at", DateTime(timezone=True)),
    Column("last_seen_at", DateTime(timezone=True)),
    Column("resolution", Text),
    Column("resolution_note", Text),
    Column("change_request_id", Uuid),
    Column("resolved_by", Uuid),
    Column("resolved_at", DateTime(timezone=True)),
    Column("waived_by", Uuid),
    Column("waived_at", DateTime(timezone=True)),
    Column("waived_reason", Text),
    Column("reopened_count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
    Column("version", Integer, nullable=False),
)

# A-01: a blocker a write without evidence removed waits for a waive holder's confirmation
# (step-up) and stays unresolved until then: certificates and exports keep refusing.
NEEDS_CONFIRMATION = "needs_confirmation"
ACTIVE_STATUSES = ("open", "reopened", NEEDS_CONFIRMATION)
