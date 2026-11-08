"""Tenant-isolation audit fixes of 2026-11-08: TI-01, TI-02, TI-04, TI-05 (FR-DPDP-021,
FR-DPDP-034, docs/08, ADR-0028 amendment 2026-11-08, ADR-0049, ADR-0050, DL-09; CLAUDE.md
invariants 1 and 6).

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
and defence in depth; ``tests/security/test_rls_catalog.py`` now pins it for every function in
the tenant and platform schemas.

TI-04. The 3-year delete floor of 0075 (``breach_records_delete_expired``) trusts
``ops.breach_records.closed_at``, which ``sos_app`` may update. ``BEFORE UPDATE`` trigger
``breach_records_closed_at`` (every role; SQLSTATE 23514 like the other register guards):

- while a breach stays ``closed`` its ``closed_at`` never changes
  (constraint ``breach_records_closed_at_fixed``);
- closing stamps the time of closing: ``closed_at`` must lie between the transaction start
  (``now()``, what the service writes) minus 5 seconds and ``clock_timestamp()`` plus 5 seconds
  (``breach_records_closed_at_now``);
- leaving ``closed`` (the SchoolOS reopen path, D49) clears it, as CHECK
  ``breach_records_closed`` already requires; re-closing restarts the clock.

INSERT is not guarded: the service inserts breaches ``open``, and restored or migrated rows keep
their history.

TI-05 (lead decision; ADR-0028 amendment of 2026-11-08). Policy ``users_in_tenant_update`` let a
school update ``core.users`` through any membership row, including an open invitation (DL-09: an
invited person is not yet the school's member) or a removal. The service already refused it
(``_guard_profile_not_shared``); now the database does too. A school may update a person's
profile only through an ``active`` or ``suspended`` membership, or when that membership is the
person's only one (``core.user_membership_count(id) = 1``: a brand-new invited account the school
created, and the offboarding clean-up of sole profiles, ``identity.repository.clear_sole_profiles``).
No new function: ``core.user_membership_count`` (ADR-0028) is already executable by ``sos_app``
and answers only for a member of the current school.

No grant or table change. Downgrade restores the 0067 trigger body, resets the two
``search_path`` settings, drops the breach trigger and its function, and restores the 0003 policy.

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

CREATE FUNCTION ops.tg_breach_records_closed_at() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF OLD.status = 'closed' AND NEW.status = 'closed'
     AND NEW.closed_at IS DISTINCT FROM OLD.closed_at THEN
    RAISE EXCEPTION 'closed_at of a closed breach never changes'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'breach_records_closed_at_fixed';
  END IF;
  IF OLD.status <> 'closed' AND NEW.status = 'closed'
     AND (NEW.closed_at < now() - interval '5 seconds'
          OR NEW.closed_at > clock_timestamp() + interval '5 seconds') THEN
    RAISE EXCEPTION 'closed_at is the time of closing'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'breach_records_closed_at_now';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ops.tg_breach_records_closed_at() FROM PUBLIC;
CREATE TRIGGER breach_records_closed_at BEFORE UPDATE ON ops.breach_records
  FOR EACH ROW EXECUTE FUNCTION ops.tg_breach_records_closed_at();

ALTER POLICY users_in_tenant_update ON core.users
  USING (EXISTS (SELECT 1 FROM core.memberships AS m
                 WHERE m.user_id = users.id AND m.tenant_id = core.current_tenant()
                   AND (m.status IN ('active', 'suspended')
                        OR core.user_membership_count(users.id) = 1)))
  WITH CHECK (EXISTS (SELECT 1 FROM core.memberships AS m
                      WHERE m.user_id = users.id AND m.tenant_id = core.current_tenant()
                        AND (m.status IN ('active', 'suspended')
                             OR core.user_membership_count(users.id) = 1)));
"""

DOWN = r"""
ALTER POLICY users_in_tenant_update ON core.users
  USING (EXISTS (SELECT 1 FROM core.memberships AS m
                 WHERE m.user_id = users.id AND m.tenant_id = core.current_tenant()))
  WITH CHECK (EXISTS (SELECT 1 FROM core.memberships AS m
                      WHERE m.user_id = users.id AND m.tenant_id = core.current_tenant()));

DROP TRIGGER IF EXISTS breach_records_closed_at ON ops.breach_records;
DROP FUNCTION IF EXISTS ops.tg_breach_records_closed_at();

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
