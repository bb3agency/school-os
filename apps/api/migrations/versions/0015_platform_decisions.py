"""Guaranteed school-chain copies of platform actions: ``platform.tenant_audit_outbox``
(FR-AUD-001, CLAUDE.md invariant 7; ADR-0020, docs/16 §16).

A control-plane action that must also appear in the school's own audit chain (``tenant.*``
lifecycle events, ``actor_type = 'platform'``) writes one row here **in the same platform
transaction** as the change and its ``platform.audit_events`` row. The maintenance task
``platform.deliver_tenant_audit`` later copies each row into the school's chain with
``audit.record()`` in that school's ``tenant_session`` and marks it delivered. ``id`` is the
dedupe key (``platform_event_id`` in the tenant event's summary), so a retry never duplicates.
``seq`` orders delivery per school.

Not tenant-owned (schema ``platform``, no RLS, no student data: IDs, codes and counts only,
validated like audit summaries before insert). Grants: ``sos_platform`` may SELECT and INSERT,
and UPDATE only ``delivered_at``, ``attempts`` and ``last_error``; no DELETE or TRUNCATE for
anyone but the owner. ``sos_app`` and ``sos_readonly`` get nothing.

Revision ID: 0015_platform_decisions
Revises: 0011_breakglass
Create Date: 2026-09-27
"""

from __future__ import annotations

from alembic import op

revision = "0015_platform_decisions"
down_revision = "0011_breakglass"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE platform.tenant_audit_outbox (
  id             uuid PRIMARY KEY,
  seq            bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  tenant_id      uuid NOT NULL,
  action         text NOT NULL CHECK (action ~ '^[a-z_]+(\.[a-z_]+)+$'),
  resource_type  text NOT NULL CHECK (resource_type ~ '^[a-z][a-z0-9_]{0,63}$'),
  resource_id    uuid,
  summary        jsonb NOT NULL CHECK (jsonb_typeof(summary) = 'object'),
  actor_id       uuid,
  request_id     text CHECK (request_id ~ '^[A-Za-z0-9._:\-]{1,128}$'),
  created_at     timestamptz NOT NULL DEFAULT now(),
  delivered_at   timestamptz,
  attempts       int NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error     text CHECK (last_error ~ '^[a-z][a-z0-9_]{0,63}$')
);
COMMENT ON TABLE platform.tenant_audit_outbox IS
  'School-chain copies of platform actions, written in the platform transaction and delivered '
  'into audit.events by platform.deliver_tenant_audit (ADR-0020). IDs and codes only.';
CREATE INDEX tenant_audit_outbox_pending
  ON platform.tenant_audit_outbox (tenant_id, seq) WHERE delivered_at IS NULL;

REVOKE ALL ON platform.tenant_audit_outbox FROM sos_platform;
GRANT SELECT, INSERT ON platform.tenant_audit_outbox TO sos_platform;
GRANT UPDATE (delivered_at, attempts, last_error) ON platform.tenant_audit_outbox TO sos_platform;
REVOKE ALL ON platform.tenant_audit_outbox FROM PUBLIC;
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS platform.tenant_audit_outbox;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    # Undelivered rows are lost with the table; deliver them first (the task runs every minute).
    op.execute(DOWNGRADE_SQL)
