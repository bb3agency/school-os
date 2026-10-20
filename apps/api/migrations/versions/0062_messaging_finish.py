"""R2-3J: withdrawing an approved parent notice (FR-NOTICE-009, FR-PUB-007; docs/05 §6.3).

``ops.parent_notices`` gains the state ``withdrawn`` and three columns that say who withdrew an
approved notice, when and why:

- ``withdrawn_by`` (FK ``core.users``), ``withdrawn_at``, ``withdraw_reason`` (10..500
  characters): all set exactly when the status is ``withdrawn`` (``parent_notices_withdrawn``).
- A withdrawn notice keeps its approval, content and rendered files: ``approved_fields``,
  ``approved_complete`` and ``render_only_approved`` now accept ``withdrawn`` next to
  ``approved``.
- ``withdrawn`` is final and only an approved notice may become withdrawn (trigger
  ``parent_notices_withdrawal_guard``): the record of what the school told parents is kept.
- ``sos_app`` may update the three new columns (the notice table is otherwise column-granted
  since 0034).

No new permission (withdrawing is ``notice.approve``), no new table, no new setting. The parent
links are revoked and the queued WhatsApp messages cancelled by the services (public, messaging).

Expand-only (invariant 12). Downgrade drops the trigger and the columns (lossy: who withdrew a
notice and why) and restores the 0037 checks as ``NOT VALID``: rows already ``withdrawn`` are
kept as they are (the migrator cannot rewrite them under FORCE RLS, as 0037), while new rows must
satisfy the old checks again.

Revision ID: 0062_messaging_finish
Revises: 0061_messaging_sends
Create Date: 2026-10-21
"""

from __future__ import annotations

from alembic import op

revision = "0062_messaging_finish"
down_revision = "0061_messaging_sends"
branch_labels = None
depends_on = None

TABLE = "ops.parent_notices"
COMPLETE = "(title_en <> '' AND body_en <> '' AND title_te <> '' AND body_te <> '')"

UPGRADE_SQL = rf"""
ALTER TABLE {TABLE}
  ADD COLUMN withdrawn_by uuid,
  ADD COLUMN withdrawn_at timestamptz,
  ADD COLUMN withdraw_reason text
    CONSTRAINT parent_notices_withdraw_reason_length
    CHECK (char_length(withdraw_reason) BETWEEN 10 AND 500);
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_withdrawn_by_fk
  FOREIGN KEY (withdrawn_by) REFERENCES core.users (id);
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_status_check;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_status_check
  CHECK (status IN ('drafting','draft','draft_failed','approved','withdrawn'));
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_approved_fields;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_approved_fields
  CHECK ((status IN ('approved','withdrawn'))
         = (approved_at IS NOT NULL AND approved_by IS NOT NULL));
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_approved_complete;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_approved_complete
  CHECK (status NOT IN ('approved','withdrawn') OR {COMPLETE});
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_render_only_approved;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_render_only_approved
  CHECK (render_status IS NULL OR status IN ('approved','withdrawn'));
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_withdrawn
  CHECK ((status = 'withdrawn')
         = (withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL
            AND withdraw_reason IS NOT NULL));

-- Only an approved notice becomes withdrawn, and withdrawn is final (FR-NOTICE-009).
CREATE FUNCTION ops.tg_parent_notices_withdrawal_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF OLD.status = 'withdrawn' AND (NEW.status <> 'withdrawn'
      OR NEW.withdrawn_by IS DISTINCT FROM OLD.withdrawn_by
      OR NEW.withdrawn_at IS DISTINCT FROM OLD.withdrawn_at
      OR NEW.withdraw_reason IS DISTINCT FROM OLD.withdraw_reason) THEN
    RAISE EXCEPTION 'a withdrawn notice stays withdrawn' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'withdrawn' AND OLD.status NOT IN ('approved','withdrawn') THEN
    RAISE EXCEPTION 'only an approved notice can be withdrawn'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER parent_notices_withdrawal_guard BEFORE UPDATE ON {TABLE}
  FOR EACH ROW EXECUTE FUNCTION ops.tg_parent_notices_withdrawal_guard();

GRANT UPDATE (withdrawn_by, withdrawn_at, withdraw_reason) ON {TABLE} TO sos_app;
"""

DOWNGRADE_SQL = rf"""
REVOKE UPDATE (withdrawn_by, withdrawn_at, withdraw_reason) ON {TABLE} FROM sos_app;
DROP TRIGGER parent_notices_withdrawal_guard ON {TABLE};
DROP FUNCTION ops.tg_parent_notices_withdrawal_guard();
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_withdrawn;
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_render_only_approved;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_render_only_approved
  CHECK (render_status IS NULL OR status = 'approved') NOT VALID;
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_approved_complete;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_approved_complete
  CHECK (status <> 'approved' OR {COMPLETE});
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_approved_fields;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_approved_fields
  CHECK ((status = 'approved') = (approved_at IS NOT NULL AND approved_by IS NOT NULL))
  NOT VALID;
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_status_check;
ALTER TABLE {TABLE} ADD CONSTRAINT parent_notices_status_check
  CHECK (status IN ('drafting','draft','draft_failed','approved')) NOT VALID;
ALTER TABLE {TABLE} DROP CONSTRAINT parent_notices_withdrawn_by_fk;
ALTER TABLE {TABLE}
  DROP COLUMN withdraw_reason,
  DROP COLUMN withdrawn_at,
  DROP COLUMN withdrawn_by;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
