"""Consent decisions keep their content when a guardian is deleted; the 0067 trigger functions pin
``search_path`` (security audit 2026-11-08, tenant isolation: TI-01, TI-02; FR-DPDP-021, docs/08,
ADR-0049; CLAUDE.md invariants 1 and 6).

TI-01. ``sis.consents.guardian_id`` references ``sis.guardians`` with ``ON DELETE SET NULL
(guardian_id)``. That referential action is an UPDATE of the decision row, which the append-only
trigger ``consents_append_only`` (0067) refused. A guardian who had once decided could therefore
never be deleted: unlinking them from their last student answered 422 ``consents_append_only``,
and their encrypted phone and address stayed (data minimisation, docs/08). The trigger now lets
exactly that action through: an UPDATE fired by another trigger (the foreign key's referential
action, ``pg_trigger_depth() > 1``) that only clears a set ``guardian_id`` and changes no other
column. The decision, its relationship, date, form and evidence stay; every other UPDATE and every
DELETE outside the offboarding purge is still refused, for every role.

TI-02. ``sis.tg_consents_append_only()`` and ``sis.tg_consent_purposes_frozen()`` were the only
trigger functions without a pinned ``search_path`` (every other one has
``search_path = pg_catalog, pg_temp``). Both use schema-qualified names, so this is consistency
and defence in depth; ``tests/security/test_definer_functions.py`` now pins it for every function
in the tenant and platform schemas.

No grant, policy or table change. Downgrade restores the 0067 trigger body and resets the two
``search_path`` settings.

Revision ID: 0080_audit_ti_fixes
Revises: 0079_audit_outbox_set_once
Create Date: 2026-11-08
"""

from __future__ import annotations

from alembic import op

revision = "0080_audit_ti_fixes"
down_revision = "0079_audit_outbox_set_once"
branch_labels = None
depends_on = None

UP = r"""
CREATE OR REPLACE FUNCTION sis.tg_consents_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' AND core.tenant_purge_allowed() THEN
    RETURN OLD;
  END IF;
  -- TI-01: consents_guardian_fk ON DELETE SET NULL (guardian_id) unlinks a deleted guardian.
  -- Only that referential action (fired by the FK trigger), only guardian_id, only to NULL.
  IF TG_OP = 'UPDATE' AND pg_catalog.pg_trigger_depth() > 1
     AND OLD.guardian_id IS NOT NULL AND NEW.guardian_id IS NULL
     AND (to_jsonb(NEW) - 'guardian_id'::text) = (to_jsonb(OLD) - 'guardian_id'::text) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'consent decisions are append-only'
    USING ERRCODE = 'check_violation', CONSTRAINT = 'consents_append_only';
END $$;
ALTER FUNCTION sis.tg_consent_purposes_frozen() SET search_path = pg_catalog, pg_temp;
"""

DOWN = r"""
CREATE OR REPLACE FUNCTION sis.tg_consents_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND core.tenant_purge_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'consent decisions are append-only'
    USING ERRCODE = 'check_violation', CONSTRAINT = 'consents_append_only';
END $$;
ALTER FUNCTION sis.tg_consents_append_only() RESET search_path;
ALTER FUNCTION sis.tg_consent_purposes_frozen() RESET search_path;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
