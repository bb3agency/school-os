"""FO-01 follow-ups on fixes outside the school and FO-03 the UDISE+ to-do list
(FR-UDI-021..FR-UDI-040; docs/05 §6.6; docs/research/2026-11-features/office-portals.md).

Two tenant tables (``tenant_id`` first, ``UNIQUE (tenant_id, id)``, RLS ENABLE + FORCE with
``tenant_isolation``, composite FKs into ``sis.students`` and ``core.memberships``, the
restrictive ``offboarding_purge`` policy with SELECT and DELETE for ``sos_purger``; ADR-0013,
ADR-0029):

- ``sis.dq_followups`` (C2): **append-only**. One row per step a person recorded while a fix
  waits outside the school: the slip given, the parent contacted (channel only, no free text,
  D56), the date the parent promised, the update the parent reported; for UDISE+ fixes the
  request sent to the MEO / MIS coordinator and the follow-up. Dates and codes only, never a
  value or an Aadhaar number (invariant 4). Trigger ``dq_followups_append_only`` refuses UPDATE
  and DELETE except in the offboarding purge. The follow-up closes by itself when the
  readiness check no longer finds the difference (computed, nothing stored).
- ``sis.udise_movement_marks`` (C1 codes and ids): the clerk's tick on an item of the UDISE+
  to-do list (``done`` or ``not_needed``). The items themselves are computed from SchoolOS
  records on every read (transfer certificates issued, students who left, admissions,
  promotions); a mark is keyed by the item's key, so ticking twice is one row. The app may
  update the mark columns and delete a mark (undo).

No permission is added: reading uses ``dq.readiness.read`` and recording uses
``dq.readiness.manage`` (lead decision D85).

Expand-only (invariant 12). Downgrade drops both tables (lossy: the follow-up log and the ticks).

Revision ID: 0088_udise_followup
Revises: 0087_legal_holds
Create Date: 2026-11-09
"""

from __future__ import annotations

from alembic import op

revision = "0088_udise_followup"
down_revision = "0087_legal_holds"
branch_labels = None
depends_on = None

# Children before parents (the order the dq module's offboarding purge deletes them in).
TENANT_TABLES = ("sis.dq_followups", "sis.udise_movement_marks")

UPGRADE_SQL = r"""
CREATE TABLE sis.dq_followups (
  id                      uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  student_id              uuid NOT NULL,
  profile_key             text NOT NULL,
  fix_owner               text NOT NULL,
  event                   text NOT NULL,
  channel                 text,
  occurred_on             date NOT NULL,
  promised_on             date,
  recorded_by             uuid NOT NULL,
  recorded_by_membership  uuid NOT NULL,
  recorded_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT dq_followups_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT dq_followups_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT dq_followups_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT dq_followups_membership_fk FOREIGN KEY (tenant_id, recorded_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT dq_followups_recorded_by_fk FOREIGN KEY (recorded_by) REFERENCES core.users (id),
  CONSTRAINT dq_followups_profile_key CHECK (profile_key ~ '^[a-z0-9][a-z0-9-]{0,63}$'),
  CONSTRAINT dq_followups_fix_owner CHECK (fix_owner IN ('parent_aadhaar','school_udise')),
  CONSTRAINT dq_followups_event CHECK (event ~ '^[a-z][a-z0-9_]{2,40}$'),
  CONSTRAINT dq_followups_channel CHECK (channel IS NULL OR channel ~ '^[a-z][a-z0-9_]{1,30}$'),
  -- A promise carries its date; no other step does.
  CONSTRAINT dq_followups_promise CHECK ((event = 'promised_by') = (promised_on IS NOT NULL)),
  CONSTRAINT dq_followups_promise_after CHECK (promised_on IS NULL OR promised_on >= occurred_on)
);
CREATE INDEX dq_followups_student_idx
  ON sis.dq_followups (tenant_id, profile_key, student_id, occurred_on);

-- Append-only: UPDATE always refused; DELETE only by the offboarding purge.
CREATE FUNCTION sis.tg_dq_followups_append_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF TG_OP = 'DELETE' AND core.tenant_purge_allowed() THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'follow-ups are append-only'
    USING ERRCODE = 'check_violation', CONSTRAINT = 'dq_followups_append_only';
END;
$$;
CREATE TRIGGER dq_followups_append_only BEFORE UPDATE OR DELETE ON sis.dq_followups
  FOR EACH ROW EXECUTE FUNCTION sis.tg_dq_followups_append_only();

CREATE TABLE sis.udise_movement_marks (
  id                    uuid PRIMARY KEY,
  tenant_id             uuid NOT NULL,
  item_key              text NOT NULL,
  kind                  text NOT NULL,
  student_id            uuid,
  state                 text NOT NULL,
  marked_by             uuid NOT NULL,
  marked_by_membership  uuid NOT NULL,
  marked_at             timestamptz NOT NULL DEFAULT now(),
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
  CONSTRAINT udise_movement_marks_tenant_id_id_key UNIQUE (tenant_id, id),
  CONSTRAINT udise_movement_marks_once UNIQUE (tenant_id, item_key),
  CONSTRAINT udise_movement_marks_tenant_fk FOREIGN KEY (tenant_id) REFERENCES core.tenants (id),
  CONSTRAINT udise_movement_marks_student_fk FOREIGN KEY (tenant_id, student_id)
    REFERENCES sis.students (tenant_id, id),
  CONSTRAINT udise_movement_marks_membership_fk FOREIGN KEY (tenant_id, marked_by_membership)
    REFERENCES core.memberships (tenant_id, id),
  CONSTRAINT udise_movement_marks_marked_by_fk FOREIGN KEY (marked_by) REFERENCES core.users (id),
  CONSTRAINT udise_movement_marks_item_key CHECK (
    item_key ~ '^[a-z_]{3,30}:[0-9a-f-]{36}$'),
  CONSTRAINT udise_movement_marks_kind CHECK (
    kind IN ('transfer_out','left_without_tc','admission','progression')),
  -- Every kind but progression (one item per promotion run) concerns one student.
  CONSTRAINT udise_movement_marks_student CHECK ((kind = 'progression') = (student_id IS NULL)),
  CONSTRAINT udise_movement_marks_state CHECK (state IN ('done','not_needed'))
);
CREATE INDEX udise_movement_marks_student_idx ON sis.udise_movement_marks (tenant_id, student_id)
  WHERE student_id IS NOT NULL;
CREATE TRIGGER udise_movement_marks_set_updated_at BEFORE UPDATE ON sis.udise_movement_marks
  FOR EACH ROW EXECUTE FUNCTION core.tg_set_updated_at();
"""

GRANTS_SQL = r"""
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON sis.dq_followups FROM sos_app;
REVOKE UPDATE, TRUNCATE, REFERENCES, TRIGGER ON sis.udise_movement_marks FROM sos_app;
GRANT UPDATE (state, marked_by, marked_by_membership, marked_at, updated_at, version)
  ON sis.udise_movement_marks TO sos_app;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = core.current_tenant()) "
            "WITH CHECK (tenant_id = core.current_tenant())"
        )
        # ADR-0029: the offboarding purge deletes these rows as sos_purger, only while the
        # school is offboarding and the transaction-local purge flag names it.
        op.execute(f"GRANT SELECT, DELETE ON {table} TO sos_purger")
        op.execute(
            f"CREATE POLICY offboarding_purge ON {table} AS RESTRICTIVE FOR ALL TO sos_purger "
            "USING (core.tenant_purge_allowed())"
        )
    op.execute(GRANTS_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sis.udise_movement_marks")
    op.execute("DROP TABLE IF EXISTS sis.dq_followups")
    op.execute("DROP FUNCTION IF EXISTS sis.tg_dq_followups_append_only()")
