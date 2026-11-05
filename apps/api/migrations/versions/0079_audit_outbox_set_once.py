"""School-chain copies: ``delivered_at`` is set once (lead decision D68; FR-AUD-001, ADR-0020,
CLAUDE.md invariant 7).

``sos_platform`` holds ``UPDATE (delivered_at, attempts, last_error)`` on
``platform.tenant_audit_outbox`` (0015) and, since 0074, ``DELETE`` of delivered rows. Without a
guard it could mark a copy delivered that it never wrote into the school's chain, and the nightly
purge would later delete it, so the school would never see that platform action.

``BEFORE UPDATE`` row trigger ``tenant_audit_outbox_set_once`` (every role; the ``platform``
schema has no RLS, so the guard is a trigger like ``tenant_audit_outbox_delivered_only``):

- ``delivered_at``, once non-NULL, can never change or be cleared
  (constraint ``tenant_audit_outbox_delivered_set_once``);
- it can only be set to a time not after ``clock_timestamp()`` plus 5 seconds of skew
  (``tenant_audit_outbox_delivered_future``) and not before the row's ``created_at``
  (``tenant_audit_outbox_delivered_early``);
- the copy itself (``id``, ``seq``, ``tenant_id``, ``action``, ``resource_type``,
  ``resource_id``, ``summary``, ``actor_id``, ``request_id``, ``created_at``) never changes
  (``tenant_audit_outbox_immutable``). ``sos_platform`` already lacks UPDATE on these columns
  (column grants, 0015); the trigger also covers the owner role. ``attempts`` and
  ``last_error`` stay writable for the deliverer.

Refusals raise SQLSTATE 55000 (``object_not_in_prerequisite_state``) with a fixed message (no
values). The second half of D68 (the purge confirms ``platform_event_id`` in the school's chain
before deleting) is application code in ``app/platform/tenant_audit.py``.

Downgrade: drops the trigger and its function (no data change).

Revision ID: 0079_audit_outbox_set_once
Revises: 0078_accept_invite_suspended
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "0079_audit_outbox_set_once"
down_revision = "0078_accept_invite_suspended"
branch_labels = None
depends_on = None

UP = r"""
CREATE FUNCTION platform.tg_tenant_audit_outbox_set_once() RETURNS trigger
  LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (NEW.id, NEW.seq, NEW.tenant_id, NEW.action, NEW.resource_type, NEW.resource_id,
      NEW.summary, NEW.actor_id, NEW.request_id, NEW.created_at)
     IS DISTINCT FROM
     (OLD.id, OLD.seq, OLD.tenant_id, OLD.action, OLD.resource_type, OLD.resource_id,
      OLD.summary, OLD.actor_id, OLD.request_id, OLD.created_at) THEN
    RAISE EXCEPTION 'school-chain copies cannot be changed, only their delivery state'
      USING ERRCODE = 'object_not_in_prerequisite_state',
            CONSTRAINT = 'tenant_audit_outbox_immutable';
  END IF;
  IF OLD.delivered_at IS NOT NULL THEN
    IF NEW.delivered_at IS DISTINCT FROM OLD.delivered_at THEN
      RAISE EXCEPTION 'delivered_at is set once and cannot be changed or cleared'
        USING ERRCODE = 'object_not_in_prerequisite_state',
              CONSTRAINT = 'tenant_audit_outbox_delivered_set_once';
    END IF;
  ELSIF NEW.delivered_at IS NOT NULL THEN
    IF NEW.delivered_at > pg_catalog.clock_timestamp() + interval '5 seconds' THEN
      RAISE EXCEPTION 'delivered_at cannot be in the future'
        USING ERRCODE = 'object_not_in_prerequisite_state',
              CONSTRAINT = 'tenant_audit_outbox_delivered_future';
    END IF;
    IF NEW.delivered_at < OLD.created_at THEN
      RAISE EXCEPTION 'delivered_at cannot be before the copy was created'
        USING ERRCODE = 'object_not_in_prerequisite_state',
              CONSTRAINT = 'tenant_audit_outbox_delivered_early';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION platform.tg_tenant_audit_outbox_set_once() FROM PUBLIC;

CREATE TRIGGER tenant_audit_outbox_set_once BEFORE UPDATE ON platform.tenant_audit_outbox
  FOR EACH ROW EXECUTE FUNCTION platform.tg_tenant_audit_outbox_set_once();
"""

DOWN = r"""
DROP TRIGGER IF EXISTS tenant_audit_outbox_set_once ON platform.tenant_audit_outbox;
DROP FUNCTION IF EXISTS platform.tg_tenant_audit_outbox_set_once();
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
