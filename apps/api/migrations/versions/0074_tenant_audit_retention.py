"""School-chain copy retention: delivered copies are deleted 30 days after delivery (lead
decision D51 extended to the control plane; FR-AUD-001, ADR-0020, docs/16 §16).

``platform.tenant_audit_outbox`` rows were never deleted (no role had DELETE). The nightly
``platform.purge_tenant_audit`` task (both deployment modes, 02:50 IST) now deletes, as
``sos_platform`` in ``core.db.platform_session()``, the copies delivered more than
``tenant_audit.delivered_retention_days`` ago (``app/platform/billing.yaml``). Undelivered copies
(``delivered_at IS NULL``) are always kept until the deliverer writes them into the school chain.

The ``platform`` schema has no RLS (CLAUDE.md invariant 1), so the guard is a trigger, as the
other platform delete guards (``plans_freeze``, ``invoices_freeze``, ``invoice_pdfs_append_only``):

- ``GRANT DELETE ON platform.tenant_audit_outbox TO sos_platform`` (only that role; ``sos_app``
  still has no privilege on the table and ``sos_platform`` none on tenant tables).
- ``BEFORE DELETE`` row trigger ``tenant_audit_outbox_delivered_only``: refuses (SQLSTATE 55000,
  constraint ``tenant_audit_outbox_undelivered``) to delete a row whose ``delivered_at IS NULL``,
  for every role, so even an unfiltered ``DELETE`` fails as a whole instead of dropping a copy
  the school has not received. No owner or offboarding path deletes these rows today, so there
  is no exception. ``TRUNCATE`` is granted to nobody.
- Partial index ``tenant_audit_outbox_delivered (delivered_at) WHERE delivered_at IS NOT NULL``
  for the nightly delete.

Deleting a delivered row can never cause a redelivery: the deliverer only claims rows with
``delivered_at IS NULL``, and the school side dedupes on ``platform_event_id`` anyway.

Downgrade: drops the index and the trigger (and its function) and revokes DELETE again (the
purge then fails and is logged; nothing else depends on it).

Revision ID: 0074_tenant_audit_retention (shortened from 0074_tenant_audit_outbox_retention:
``ops.alembic_version.version_num`` is varchar(32))
Revises: 0073_admission_no_ci
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "0074_tenant_audit_retention"
down_revision = "0073_admission_no_ci"
branch_labels = None
depends_on = None

UP = r"""
CREATE INDEX tenant_audit_outbox_delivered
  ON platform.tenant_audit_outbox (delivered_at) WHERE delivered_at IS NOT NULL;

CREATE FUNCTION platform.tg_tenant_audit_outbox_delivered_only() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF OLD.delivered_at IS NULL THEN
    RAISE EXCEPTION 'undelivered school-chain copies cannot be deleted'
      USING ERRCODE = 'object_not_in_prerequisite_state',
            CONSTRAINT = 'tenant_audit_outbox_undelivered';
  END IF;
  RETURN OLD;
END
$$;
REVOKE ALL ON FUNCTION platform.tg_tenant_audit_outbox_delivered_only() FROM PUBLIC;

CREATE TRIGGER tenant_audit_outbox_delivered_only BEFORE DELETE ON platform.tenant_audit_outbox
  FOR EACH ROW EXECUTE FUNCTION platform.tg_tenant_audit_outbox_delivered_only();

GRANT DELETE ON platform.tenant_audit_outbox TO sos_platform;
"""

DOWN = r"""
REVOKE DELETE ON platform.tenant_audit_outbox FROM sos_platform;
DROP TRIGGER IF EXISTS tenant_audit_outbox_delivered_only ON platform.tenant_audit_outbox;
DROP FUNCTION IF EXISTS platform.tg_tenant_audit_outbox_delivered_only();
DROP INDEX IF EXISTS platform.tenant_audit_outbox_delivered;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
