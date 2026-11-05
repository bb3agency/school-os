"""Outbox retention: delivered events are deleted 30 days after delivery (lead decision D51;
FR-OPS-004, docs/05 §13, docs/10).

``ops.outbox`` rows were never deleted. The nightly ops purge (beat
``ops.purge_idempotency_keys``, 02:40 IST) now also deletes, per school in its own
``tenant_session`` (``sos_app``, RLS), the events dispatched more than
``outbox.delivered_retention_days`` ago (``app/ops/config.yaml``). Undelivered events
(``dispatched_at IS NULL``) are kept until the dispatcher relays them.

- ``GRANT DELETE ON ops.outbox TO sos_app`` (0006 revoked it: rows were never edited). INSERT
  and SELECT are unchanged; UPDATE stays with ``sos_definer`` (``dispatched_at`` only).
- Restrictive policy ``outbox_delete_dispatched`` (``FOR DELETE TO sos_app``): the app role may
  delete only dispatched rows, so a bug can never drop an event before it was relayed. The
  ``tenant_isolation`` policy still applies (RLS ENABLE + FORCE unchanged). The offboarding
  purge deletes as ``sos_purger`` (0032), which this policy does not cover.
- Index ``outbox_dispatched (tenant_id, dispatched_at) WHERE dispatched_at IS NOT NULL`` for the
  nightly delete.

No audit table changes (they stay append-only). No personal data (payloads carry IDs only).

Downgrade: drops the index and the policy and revokes DELETE again (the purge then fails per
school and is logged; nothing else depends on it).

Revision ID: 0072_outbox_retention
Revises: 0071_subscription_billing_anchor
Create Date: 2026-11-04
"""

from __future__ import annotations

from alembic import op

revision = "0072_outbox_retention"
down_revision = "0071_subscription_billing_anchor"
branch_labels = None
depends_on = None

UP = r"""
CREATE INDEX outbox_dispatched
  ON ops.outbox (tenant_id, dispatched_at) WHERE dispatched_at IS NOT NULL;
CREATE POLICY outbox_delete_dispatched ON ops.outbox AS RESTRICTIVE FOR DELETE TO sos_app
  USING (dispatched_at IS NOT NULL);
GRANT DELETE ON ops.outbox TO sos_app;
"""

DOWN = r"""
REVOKE DELETE ON ops.outbox FROM sos_app;
DROP POLICY IF EXISTS outbox_delete_dispatched ON ops.outbox;
DROP INDEX IF EXISTS ops.outbox_dispatched;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
