"""Per-student add-on fees: transport bands, hostel, activities (FM-02; FR-FEE-081..100;
ADR-0042 follow-up; lead decision D85).

- ``fin.fee_addons``: an add-on template per academic year: one fee head (collectable), a label
  such as "Band 2 (5-10 km)" or "Hostel", and an amount per instalment number (JSON object
  ``{"1": "1500.00", ...}``). The head never changes; the amounts change only while nobody is
  assigned (the service locks the template for both).
- ``fin.fee_addon_assignments``: one student's fee account on one template from a start
  instalment. ``head_id`` is a copy of the template's head (composite FK to
  ``(tenant_id, id, head_id)`` of the template) so that one partial unique index allows at most
  one active add-on per head per account (one transport band at a time). An assignment only ends
  (``active -> ended`` with ``end_instalment``, trigger ``fee_addon_assignments_frozen``).
- ``fin.ledger_entries.addon_assignment_id``: an add-on charge is a ``charge`` entry whose source
  is the assignment (CHECK ``ledger_entries_addon_kind``), on the assignment's own account and
  head (composite FK). Plan charges stay unique per (account, instalment, head)
  (``ledger_entries_one_charge`` now excludes add-on charges) and add-on charges are unique per
  (assignment, instalment), so a plan and an add-on may charge the same head and both are
  idempotent (FR-FEE-005).
- ``fin.adjustments``: reason ``addon_removal`` (a credit of an add-on charge when a student is
  taken off mid-year; maker-checker like every reduction, D85) names its assignment
  (``addon_assignment_id``, same composite FK) and lowers what is due; one standing removal per
  (assignment, instalment).
- Permission ``fee.addon.read`` (who is on which add-on, without amounts) is upserted into
  ``core.permissions``; role grants of existing schools come from
  ``python -m app.identity.sync_system_roles`` (ADR-0022).

Tenant-table rules for both new tables: ``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS
ENABLE + FORCE with ``tenant_isolation``, composite FKs, the restrictive ``offboarding_purge``
policy for ``sos_purger`` (ADR-0029), no ``definer_access``, nothing for ``sos_platform``;
``sos_app`` has SELECT, INSERT and UPDATE of the listed columns only, no DELETE.

Expand-only (invariant 12). Downgrade fails while any add-on charge or removal exists (the
CHECKs it restores are validated against every row): posted ledger facts are never rewritten.
Otherwise it drops the tables, columns and the permission (unless a role still holds it).

Revision ID: 0088_fee_addons
Revises: 0087_legal_holds
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0088_fee_addons"
down_revision = "0087_legal_holds"
branch_labels = None
depends_on = None

TABLES = ("fin.fee_addons", "fin.fee_addon_assignments")

_OLD_REASONS = (
    "('late_fee_waiver','plan_change','concession_reversal','correction','write_off','other')"
)
_NEW_REASONS = (
    "('late_fee_waiver','plan_change','concession_reversal','correction','write_off',"
    "'addon_removal','other')"
)

UPGRADE_SQL = """
CREATE TABLE fin.fee_addons (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  academic_year_id  uuid NOT NULL,
  head_id           uuid NOT NULL,
  label             text NOT NULL,
  amounts           jsonb NOT NULL,
  active            boolean NOT NULL DEFAULT true,
  created_by        uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  version           int NOT NULL DEFAULT 1,
  CONSTRAINT fee_addons_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT fee_addons_id_head_key UNIQUE (tenant_id, id, head_id),
  CONSTRAINT fee_addons_label_unique UNIQUE (tenant_id, academic_year_id, label),
  CONSTRAINT fee_addons_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT fee_addons_year_fk FOREIGN KEY (tenant_id, academic_year_id)
    REFERENCES core.academic_years (tenant_id, id),
  CONSTRAINT fee_addons_head_fk FOREIGN KEY (tenant_id, head_id)
    REFERENCES fin.fee_heads (tenant_id, id),
  CONSTRAINT fee_addons_created_by_fk FOREIGN KEY (tenant_id, created_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT fee_addons_label_shape
    CHECK (char_length(label) BETWEEN 1 AND 80),
  CONSTRAINT fee_addons_amounts_object
    CHECK (jsonb_typeof(amounts) = 'object' AND amounts <> '{}'::jsonb),
  CONSTRAINT fee_addons_version_positive CHECK (version >= 1)
);
CREATE INDEX fee_addons_year_idx ON fin.fee_addons (tenant_id, academic_year_id);
CREATE TRIGGER fee_addons_set_updated_at BEFORE UPDATE ON fin.fee_addons
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

CREATE TABLE fin.fee_addon_assignments (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  addon_id          uuid NOT NULL,
  account_id        uuid NOT NULL,
  head_id           uuid NOT NULL,
  start_instalment  int NOT NULL,
  end_instalment    int,
  status            text NOT NULL DEFAULT 'active',
  assigned_by       uuid NOT NULL,
  assigned_at       timestamptz NOT NULL DEFAULT now(),
  ended_by          uuid,
  ended_at          timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  version           int NOT NULL DEFAULT 1,
  CONSTRAINT fee_addon_assignments_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT fee_addon_assignments_source_key UNIQUE (tenant_id, id, account_id, head_id),
  CONSTRAINT fee_addon_assignments_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT fee_addon_assignments_addon_fk FOREIGN KEY (tenant_id, addon_id, head_id)
    REFERENCES fin.fee_addons (tenant_id, id, head_id),
  CONSTRAINT fee_addon_assignments_account_fk FOREIGN KEY (tenant_id, account_id)
    REFERENCES fin.student_fee_accounts (tenant_id, id),
  CONSTRAINT fee_addon_assignments_head_fk FOREIGN KEY (tenant_id, head_id)
    REFERENCES fin.fee_heads (tenant_id, id),
  CONSTRAINT fee_addon_assignments_assigned_by_fk FOREIGN KEY (tenant_id, assigned_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT fee_addon_assignments_ended_by_fk FOREIGN KEY (tenant_id, ended_by)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT fee_addon_assignments_start_range CHECK (start_instalment BETWEEN 1 AND 24),
  -- Ending before the start instalment (end = start - 1) means nothing is owed.
  CONSTRAINT fee_addon_assignments_end_range CHECK (end_instalment IS NULL
    OR (end_instalment BETWEEN 0 AND 24 AND end_instalment >= start_instalment - 1)),
  CONSTRAINT fee_addon_assignments_status_check CHECK (status IN ('active','ended')),
  CONSTRAINT fee_addon_assignments_ended CHECK ((status = 'ended') = (end_instalment IS NOT NULL)
    AND (end_instalment IS NULL) = (ended_by IS NULL)
    AND (ended_by IS NULL) = (ended_at IS NULL)),
  CONSTRAINT fee_addon_assignments_version_positive CHECK (version >= 1)
);
-- FR-FEE-086: at most one active add-on per head per account (one transport band at a time).
CREATE UNIQUE INDEX fee_addon_assignments_one_per_head ON fin.fee_addon_assignments
  (tenant_id, account_id, head_id) WHERE status = 'active';
CREATE INDEX fee_addon_assignments_addon_idx ON fin.fee_addon_assignments
  (tenant_id, addon_id, status);
CREATE TRIGGER fee_addon_assignments_set_updated_at BEFORE UPDATE ON fin.fee_addon_assignments
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();

-- FR-FEE-089: an assignment only ends, once; what it was never changes.
CREATE FUNCTION fin.tg_fee_addon_assignments_frozen() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (NEW.id, NEW.tenant_id, NEW.addon_id, NEW.account_id, NEW.head_id, NEW.start_instalment,
      NEW.assigned_by, NEW.assigned_at)
     IS DISTINCT FROM
     (OLD.id, OLD.tenant_id, OLD.addon_id, OLD.account_id, OLD.head_id, OLD.start_instalment,
      OLD.assigned_by, OLD.assigned_at)
     OR (OLD.status = 'ended' AND (NEW.status, NEW.end_instalment, NEW.ended_by, NEW.ended_at)
         IS DISTINCT FROM (OLD.status, OLD.end_instalment, OLD.ended_by, OLD.ended_at)) THEN
    RAISE EXCEPTION 'an add-on assignment only ends, once'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'fee_addon_assignments_frozen';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER fee_addon_assignments_frozen BEFORE UPDATE ON fin.fee_addon_assignments
  FOR EACH ROW EXECUTE FUNCTION fin.tg_fee_addon_assignments_frozen();

-- Add-on charges: a charge entry whose source is an assignment, on its account and head.
ALTER TABLE fin.ledger_entries ADD COLUMN addon_assignment_id uuid;
ALTER TABLE fin.ledger_entries ADD CONSTRAINT ledger_entries_addon_fk
  FOREIGN KEY (tenant_id, addon_assignment_id, account_id, head_id)
  REFERENCES fin.fee_addon_assignments (tenant_id, id, account_id, head_id);
ALTER TABLE fin.ledger_entries ADD CONSTRAINT ledger_entries_addon_kind
  CHECK (addon_assignment_id IS NULL OR entry_kind = 'charge');
DROP INDEX fin.ledger_entries_one_charge;
CREATE UNIQUE INDEX ledger_entries_one_charge ON fin.ledger_entries
  (tenant_id, account_id, instalment_no, head_id)
  WHERE entry_kind = 'charge' AND addon_assignment_id IS NULL;
CREATE UNIQUE INDEX ledger_entries_one_addon_charge ON fin.ledger_entries
  (tenant_id, addon_assignment_id, instalment_no) WHERE addon_assignment_id IS NOT NULL;

-- Taking a student off an add-on mid-year: credits as maker-checker adjustments (D85).
ALTER TABLE fin.adjustments ADD COLUMN addon_assignment_id uuid;
ALTER TABLE fin.adjustments ADD CONSTRAINT adjustments_addon_fk
  FOREIGN KEY (tenant_id, addon_assignment_id, account_id, head_id)
  REFERENCES fin.fee_addon_assignments (tenant_id, id, account_id, head_id);
ALTER TABLE fin.adjustments DROP CONSTRAINT adjustments_reason_code_check;
ALTER TABLE fin.adjustments ADD CONSTRAINT adjustments_reason_code_check
  CHECK (reason_code IN __NEW_REASONS__);
ALTER TABLE fin.adjustments ADD CONSTRAINT adjustments_addon_removal_shape
  CHECK ((reason_code = 'addon_removal') = (addon_assignment_id IS NOT NULL)
    AND (reason_code <> 'addon_removal' OR (head_id IS NOT NULL AND amount < 0)));
CREATE UNIQUE INDEX adjustments_one_addon_removal ON fin.adjustments
  (tenant_id, addon_assignment_id, instalment_no)
  WHERE addon_assignment_id IS NOT NULL AND status IN ('pending', 'approved');
""".replace("__NEW_REASONS__", _NEW_REASONS)

GRANTS_SQL = """
REVOKE UPDATE, DELETE, TRUNCATE ON fin.fee_addons FROM sos_app;
GRANT UPDATE (label, amounts, active, updated_at, version) ON fin.fee_addons TO sos_app;
REVOKE ALL ON fin.fee_addons FROM sos_readonly;
GRANT SELECT ON fin.fee_addons TO sos_readonly;
REVOKE UPDATE, DELETE, TRUNCATE ON fin.fee_addon_assignments FROM sos_app;
GRANT UPDATE (status, end_instalment, ended_by, ended_at, updated_at, version)
  ON fin.fee_addon_assignments TO sos_app;
REVOKE ALL ON fin.fee_addon_assignments FROM sos_readonly;
GRANT SELECT ON fin.fee_addon_assignments TO sos_readonly;
"""

NEW_PERMISSIONS: tuple[dict[str, object], ...] = (
    {
        "key": "fee.addon.read",
        "description": (
            "See which students are on each add-on fee (transport band, hostel, activity), "
            "without amounts"
        ),
        "sensitivity": "sensitive",
        "step_up": False,
        "is_platform": False,
    },
)

UPSERT = sa.text(
    """
    INSERT INTO core.permissions (key, description, sensitivity, step_up, is_platform)
    VALUES (:key, :description, :sensitivity, :step_up, :is_platform)
    ON CONFLICT (key) DO UPDATE SET
      description = EXCLUDED.description,
      sensitivity = EXCLUDED.sensitivity,
      step_up = EXCLUDED.step_up,
      is_platform = EXCLUDED.is_platform
    """
)

DOWNGRADE_SQL = f"""
DROP INDEX IF EXISTS fin.adjustments_one_addon_removal;
ALTER TABLE fin.adjustments DROP CONSTRAINT IF EXISTS adjustments_addon_removal_shape;
ALTER TABLE fin.adjustments DROP CONSTRAINT adjustments_reason_code_check;
ALTER TABLE fin.adjustments ADD CONSTRAINT adjustments_reason_code_check
  CHECK (reason_code IN {_OLD_REASONS});
ALTER TABLE fin.adjustments ADD CONSTRAINT adjustments_no_addon
  CHECK (addon_assignment_id IS NULL);
ALTER TABLE fin.adjustments DROP CONSTRAINT adjustments_no_addon;
ALTER TABLE fin.adjustments DROP CONSTRAINT IF EXISTS adjustments_addon_fk;
ALTER TABLE fin.adjustments DROP COLUMN IF EXISTS addon_assignment_id;

ALTER TABLE fin.ledger_entries ADD CONSTRAINT ledger_entries_no_addon
  CHECK (addon_assignment_id IS NULL);
ALTER TABLE fin.ledger_entries DROP CONSTRAINT ledger_entries_no_addon;
DROP INDEX IF EXISTS fin.ledger_entries_one_addon_charge;
DROP INDEX fin.ledger_entries_one_charge;
CREATE UNIQUE INDEX ledger_entries_one_charge ON fin.ledger_entries
  (tenant_id, account_id, instalment_no, head_id) WHERE entry_kind = 'charge';
ALTER TABLE fin.ledger_entries DROP CONSTRAINT IF EXISTS ledger_entries_addon_kind;
ALTER TABLE fin.ledger_entries DROP CONSTRAINT IF EXISTS ledger_entries_addon_fk;
ALTER TABLE fin.ledger_entries DROP COLUMN IF EXISTS addon_assignment_id;

DROP TABLE IF EXISTS fin.fee_addon_assignments;
DROP FUNCTION IF EXISTS fin.tg_fee_addon_assignments_frozen();
DROP TABLE IF EXISTS fin.fee_addons;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
        # ADR-0029: the offboarding purge deletes these rows as sos_purger.
        op.execute(f"GRANT SELECT, DELETE ON {table} TO sos_purger")
        op.execute(
            f"CREATE POLICY offboarding_purge ON {table} AS RESTRICTIVE FOR ALL TO sos_purger "
            "USING (core.tenant_purge_allowed())"
        )
    op.execute(GRANTS_SQL)
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        bind.execute(UPSERT, row)


def downgrade() -> None:
    bind = op.get_bind()
    for row in NEW_PERMISSIONS:
        savepoint = bind.begin_nested()
        try:
            bind.execute(sa.text("DELETE FROM core.permissions WHERE key = :k"), {"k": row["key"]})
            savepoint.commit()
        except sa.exc.IntegrityError:
            savepoint.rollback()
    op.execute(DOWNGRADE_SQL)
